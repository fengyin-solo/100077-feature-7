"""病害登记业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from app.store import store

MODULE = "disease"
REQUIRED_FIELDS = ["病害编号", "所在设施", "病害类型"]
STATUS_ORDER = ["待定级", "已定级", "处置中", "已闭环", "已挂起"]
ACTION_RULES = {"确认定级": "已定级", "提交闭环": "已闭环", "挂起病害": "已挂起"}
NEGATIVE_ACTIONS = []

# 批量导入的固定模板列：顺序与模板文件保持一致，病害状态由系统置为待定级，不接受导入。
IMPORT_FIELDS = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员"]
# 病害类型允许清单：导入时逐条比对，不在清单里的行不落库。
ALLOWED_DISEASE_TYPES = [
    "裂缝", "坑槽", "车辙", "沉陷", "松散", "泛油", "啃边",
    "渗水", "伸缩缝损坏", "支座老化", "铺装破损", "混凝土剥落", "钢筋锈蚀", "排水堵塞",
]
# 所在设施的存在性校验范围：道路、桥梁、隧道台账里的编码或名称都算命中。
FACILITY_MODULES = {
    "road": ["设施编码", "道路名称"],
    "bridge": ["桥梁编码", "桥梁名称"],
    "tunnel": ["隧道编码", "隧道名称"],
}


class DiseaseService:
    def __init__(self) -> None:
        # 已导入文件的内容指纹 -> 当次导入结果：重复导入同一份文件时直接返回原结果，不再落库。
        self._batches: dict[str, dict[str, Any]] = {}
        self._batch_seq = 0

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("病害编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"病害记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于病害登记可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"病害记录已{action}"

    def import_template(self) -> str:
        """生成固定模板的 CSV 文本：只有表头，避免示例行被误导入。"""
        buffer = io.StringIO()
        csv.writer(buffer).writerow(IMPORT_FIELDS)
        return buffer.getvalue()

    def import_entries(self, content: str, source: str | None = None) -> dict[str, Any]:
        """按固定模板批量导入病害记录。

        逐行校验：必填字段、病害编号与台账及文件内是否重复、所在设施是否存在、
        病害类型是否在允许清单；有问题的行不落库并逐条说明原因，其余行正常登记。
        同一份文件（内容指纹相同）重复导入时直接返回上次结果，不会生成两份记录。
        """
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cached = self._batches.get(fingerprint)
        if cached is not None:
            return {**cached, "duplicated": True, "message": f"{cached['message']}；该文件已导入过，本次未重复登记"}

        rows, error = self._parse_template(content)
        if error is not None:
            return {"ok": False, "message": error, "batch": None, "imported": [], "failed": [], "duplicated": False}

        existing = {str(row.get("病害编号") or "").strip() for row in store.rows(MODULE)}
        seen: set[str] = set()
        imported: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for line_no, raw in rows:
            values = {field: str(raw.get(field) or "").strip() for field in IMPORT_FIELDS}
            problems = self.validate_import_row(values, existing, seen)
            code = values["病害编号"]
            if problems:
                failed.append({"line": line_no, "key": code or f"第{line_no}行", "reason": "；".join(problems)})
                continue
            seen.add(code)
            imported.append(self._create_imported(values))

        self._batch_seq += 1
        batch = f"IMP-{self._batch_seq:04d}"
        message = f"导入完成：成功 {len(imported)} 条，跳过 {len(failed)} 条"
        result = {
            "ok": not failed,
            "message": message,
            "batch": batch,
            "imported": imported,
            "failed": failed,
            "duplicated": False,
        }
        if imported:
            # 只有真正落库过的文件才记指纹：全部失败的文件重传时应重新逐条校验。
            self._batches[fingerprint] = result
        return result

    def validate_import_row(
        self,
        values: dict[str, str],
        existing: set[str],
        seen: set[str],
    ) -> list[str]:
        """校验一行导入数据，返回该行的全部问题（空列表表示可以落库）。"""
        problems: list[str] = []
        missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
        if missing:
            problems.append(f"缺少必填字段：{'、'.join(missing)}")
        code = values.get("病害编号", "")
        if code:
            if code in existing:
                problems.append(f"病害编号 {code} 已在台账中存在")
            elif code in seen:
                problems.append(f"病害编号 {code} 在文件内重复")
        facility = values.get("所在设施", "")
        if facility and not self._facility_exists(facility):
            problems.append(f"所在设施 {facility} 在道路、桥梁、隧道台账中都不存在")
        disease_type = values.get("病害类型", "")
        if disease_type and disease_type not in ALLOWED_DISEASE_TYPES:
            problems.append(f"病害类型 {disease_type} 不在允许清单（{'、'.join(ALLOWED_DISEASE_TYPES)}）")
        return problems

    def stats_by_facility(self) -> list[dict[str, Any]]:
        """按所在设施统计病害记录数量，台账实时计算，导入后自然跟着更新。"""
        counter: dict[str, int] = {}
        for row in store.rows(MODULE):
            key = str(row.get("所在设施") or "").strip() or "未填写"
            counter[key] = counter.get(key, 0) + 1
        return [
            {"所在设施": facility, "数量": count}
            for facility, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _parse_template(self, content: str) -> tuple[list[tuple[int, dict[str, str]]], str | None]:
        """解析模板 CSV：校验表头齐全，跳过空行，行号从 2 起算方便对照表格。"""
        text = content.removeprefix("\ufeff")  # Excel 导出的 CSV 常带 BOM
        if not text.strip():
            return [], "导入内容为空，请按模板填写后再提交"
        reader = csv.reader(io.StringIO(text))
        try:
            header = [cell.strip() for cell in next(reader)]
        except StopIteration:
            return [], "导入内容为空，请按模板填写后再提交"
        missing_columns = [field for field in REQUIRED_FIELDS if field not in header]
        if missing_columns:
            return [], f"模板列缺失：{'、'.join(missing_columns)}，请使用系统提供的导入模板"
        rows: list[tuple[int, dict[str, str]]] = []
        for line_no, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            rows.append((line_no, dict(zip(header, (cell.strip() for cell in cells)))))
        if not rows:
            return [], "模板里没有数据行，请至少填写一条病害记录"
        return rows, None

    def _facility_exists(self, name: str) -> bool:
        for module, fields in FACILITY_MODULES.items():
            for row in store.rows(module):
                if any(str(row.get(field) or "").strip() == name for field in fields):
                    return True
        return False

    def _create_imported(self, values: dict[str, str]) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in IMPORT_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry

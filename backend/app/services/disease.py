"""病害登记业务规则：状态流转、字段校验、批量导入与台账统计口径都收在这里。"""
from __future__ import annotations

import csv
import io
from typing import Any

from app.store import store

MODULE = "disease"
REQUIRED_FIELDS = ["病害编号", "所在设施", "病害类型"]
STATUS_ORDER = ["待定级", "已定级", "处置中", "已闭环", "已挂起"]
ACTION_RULES = {"确认定级": "已定级", "提交闭环": "已闭环", "挂起病害": "已挂起"}
NEGATIVE_ACTIONS = []

# 批量导入的固定模板列，顺序即模板里的列顺序；病害状态由系统流转维护，不开放导入。
TEMPLATE_FIELDS = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员"]
TEMPLATE_SAMPLE = ["DISE-0000", "ROAD-0001", "裂缝", "K0+200 右侧车道", "一般", "2026-09-01", "张三"]

# 病害类型允许清单：批量导入只接受清单内的取值，清单外的一律不落库。
ALLOWED_DISEASE_TYPES = [
    "裂缝", "坑槽", "车辙", "沉陷", "松散", "泛油", "麻面",
    "露筋", "渗水", "支座损坏", "伸缩缝损坏", "护栏损坏", "排水不畅",
]

# 所在设施的认定口径：道路、桥梁、隧道三类档案里的编码与名称都算有效设施。
FACILITY_SOURCES = {
    "road": ["设施编码", "道路名称"],
    "bridge": ["桥梁编码", "桥梁名称"],
    "tunnel": ["隧道编码", "隧道名称"],
}


class DiseaseService:
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
        return self._build_entry(values), []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """修改单条记录的台账字段；状态流转不在这里改，仍走动作接口。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"病害记录 {entry_id} 不存在或已归档"]
        updates = {
            field: str(values.get(field) or "").strip()
            for field in TEMPLATE_FIELDS
            if field in values
        }
        errors = [f"{field}不能为空" for field in REQUIRED_FIELDS if field in updates and not updates[field]]
        code = updates.get("病害编号")
        if code and any(
            row is not entry and str(row.get("病害编号") or "").strip() == code
            for row in store.rows(MODULE)
        ):
            errors.append(f"病害编号 {code} 已被其他记录使用")
        if errors:
            return None, errors
        entry.update(updates)
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

    def template_csv(self) -> str:
        """固定导入模板：表头加一行示例，带 BOM 方便 Excel 直接打开不乱码。"""
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(TEMPLATE_FIELDS)
        writer.writerow(TEMPLATE_SAMPLE)
        return "\ufeff" + buffer.getvalue()

    def parse_template(self, content: str) -> tuple[list[dict[str, Any]], list[str]]:
        """把模板 CSV 文本解析成数据行；表头缺列时整个文件拒绝并说明缺哪几列。"""
        text = content.lstrip("\ufeff")
        if not text.strip():
            return [], ["文件内容为空，请按模板填写后再导入"]
        reader = csv.reader(io.StringIO(text))
        header = [str(cell).strip() for cell in next(reader, [])]
        missing = [field for field in TEMPLATE_FIELDS if field not in header]
        if missing:
            return [], [f"模板表头缺少列：{'、'.join(missing)}，请下载最新模板后重试"]
        rows: list[dict[str, Any]] = []
        for line, record in enumerate(reader, start=2):
            if not any(str(cell).strip() for cell in record):
                continue
            values = {
                field: str(record[header.index(field)]).strip() if header.index(field) < len(record) else ""
                for field in TEMPLATE_FIELDS
            }
            rows.append({"line": line, "values": values})
        if not rows:
            return [], ["模板里没有可导入的数据行"]
        return rows, []

    def import_entries(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """逐行校验后落库：有问题的行不写入并逐条说明原因，合格的行正常登记。

        病害编号与台账内已有记录查重，同一份文件重复导入时整批判重、不会生成第二份。
        """
        existing = {str(row.get("病害编号") or "").strip() for row in store.rows(MODULE)}
        facilities = self._facility_index()
        seen_in_file: set[str] = set()
        imported: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for row in rows:
            values = row["values"]
            reasons: list[str] = []
            missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
            if missing:
                reasons.append(f"缺少必填字段：{'、'.join(missing)}")
            code = values.get("病害编号", "")
            if code:
                if code in seen_in_file:
                    reasons.append("病害编号在文件内重复，以先出现的行为准")
                elif code in existing:
                    reasons.append("病害编号已存在，疑似重复导入")
            facility = values.get("所在设施", "")
            if facility and facility not in facilities:
                reasons.append("所在设施不在道路、桥梁、隧道档案里")
            disease_type = values.get("病害类型", "")
            if disease_type and disease_type not in ALLOWED_DISEASE_TYPES:
                reasons.append("病害类型不在允许清单内")
            if reasons:
                failures.append({"line": row["line"], "病害编号": code or "—", "reasons": reasons})
                continue
            seen_in_file.add(code)
            imported.append(self._build_entry(values))
        return imported, failures

    def facility_stats(self) -> list[dict[str, Any]]:
        """台账统计：按所在设施汇总病害数量与各状态分布，随台账实时计算。"""
        stats: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            facility = str(row.get("所在设施") or "").strip() or "未填写"
            item = stats.setdefault(
                facility,
                {"所在设施": facility, "总数": 0, **{status: 0 for status in STATUS_ORDER}},
            )
            item["总数"] += 1
            status = str(row.get("status") or "")
            if status in STATUS_ORDER:
                item[status] += 1
        return sorted(stats.values(), key=lambda item: (-int(item["总数"]), str(item["所在设施"])))

    def _build_entry(self, values: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in TEMPLATE_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry

    def _facility_index(self) -> set[str]:
        names: set[str] = set()
        for module, fields in FACILITY_SOURCES.items():
            for row in store.rows(module):
                for field in fields:
                    value = str(row.get(field) or "").strip()
                    if value:
                        names.add(value)
        return names

"""病害登记接口：维护病害记录，覆盖确认定级、提交闭环、挂起病害等动作，支持按固定模板批量导入。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from app.schemas import ActionResult, EntryPayload, ImportPayload, ImportResult, PageResult
from app.services.disease import DiseaseService

router = APIRouter(prefix="/api/disease", tags=["病害登记"])

service = DiseaseService()

LIST_FIELDS = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员", "病害状态"]
STATUSES = ["待定级", "已定级", "处置中", "已闭环", "已挂起"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按病害编号检索"),
    status: str | None = Query(default=None, description="待定级、已定级、处置中、已闭环、已挂起"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按病害编号与状态过滤病害登记列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路径要放在 /{entry_id} 之前，否则会被路径参数截获。

@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出病害登记清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "disease", "total": total, "items": items}


@router.get("/stats")
def facility_stats() -> dict[str, Any]:
    """台账统计：按所在设施汇总病害数量，导入完成后随台账实时更新。"""
    items = service.facility_stats()
    return {"module": "disease", "total": sum(int(item["总数"]) for item in items), "items": items}


@router.get("/import/template")
def download_template() -> Response:
    """下载批量导入的固定模板（CSV，含一行示例，导入前请删除示例行）。"""
    return Response(
        content=service.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=disease_import_template.csv"},
    )


@router.post("/import", response_model=ImportResult)
def import_entries(payload: ImportPayload) -> ImportResult:
    """按固定模板批量导入：逐行校验，合格的落库，有问题的不落库并逐条说明原因。

    病害编号与台账查重，同一份文件重复导入不会生成第二份记录。
    """
    rows, errors = service.parse_template(payload.content)
    if errors:
        return ImportResult(ok=False, message="；".join(errors))
    imported, failures = service.import_entries(rows)
    if not failures:
        message = f"成功导入 {len(imported)} 条病害记录"
    elif not imported:
        message = f"没有记录被导入：{len(failures)} 条未通过校验（病害编号查重可防重复导入）"
    else:
        message = f"共 {len(rows)} 行：成功导入 {len(imported)} 条，{len(failures)} 条未通过校验未落库"
    return ImportResult(
        ok=bool(imported),
        message=message,
        total=len(rows),
        imported=len(imported),
        failed=len(failures),
        failures=failures,
        items=imported,
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条病害记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"病害记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条病害记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="病害记录已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改单条病害记录的台账字段；编号重复或必填为空时说明原因，状态流转仍走动作接口。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="病害记录已更新", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条病害记录执行确认定级、提交闭环、挂起病害；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

<template>
  <section class="page" data-module="disease">
    <header class="page-head">
      <div>
        <h2>病害登记管理</h2>
        <p class="page-desc">维护病害记录，围绕病害编号、所在设施、病害类型、病害位置做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记病害记录</button>
        <button class="btn" type="button" @click="triggerImport">批量导入</button>
        <button class="btn" type="button" @click="downloadTemplate">下载导入模板</button>
        <button class="btn" type="button" @click="exportRows">导出病害登记清单</button>
        <input ref="fileInput" type="file" accept=".csv" hidden @change="handleImportFile" />
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section v-if="importResult" class="panel">
      <h3 class="panel-title">批量导入结果</h3>
      <p class="panel-desc">
        {{ importResult.message }}（共 {{ importResult.total }} 行，成功 {{ importResult.imported }} 条，未落库 {{ importResult.failed }} 条）
      </p>
      <table v-if="importResult.failures.length" class="data-table">
        <thead>
          <tr><th>行号</th><th>病害编号</th><th>未落库原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="failure in importResult.failures" :key="failure.line">
            <td>第 {{ failure.line }} 行</td>
            <td>{{ failure['病害编号'] }}</td>
            <td>{{ failure.reasons.join('；') }}</td>
          </tr>
        </tbody>
      </table>
      <p class="panel-desc muted">
        允许的病害类型：{{ allowedTypesText }}。所在设施需为道路、桥梁、隧道档案里的编码或名称；重复导入同一份文件不会生成第二份记录。
      </p>
    </section>

    <section class="panel">
      <h3 class="panel-title">病害台账统计（按所在设施）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>所在设施</th>
            <th>总数</th>
            <th v-for="status in statuses" :key="status">{{ status }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in facilityStats" :key="String(item['所在设施'])">
            <td>{{ item['所在设施'] }}</td>
            <td>{{ item['总数'] }}</td>
            <td v-for="status in statuses" :key="status">{{ item[status] ?? 0 }}</td>
          </tr>
          <tr v-if="!facilityStats.length">
            <td :colspan="statuses.length + 2" class="empty-state">暂无台账数据</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openEdit(row)">编辑</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无病害登记数据，可先登记病害记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条病害登记记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="dialog.visible" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3 class="panel-title">{{ dialog.mode === 'create' ? '登记病害记录' : '修改病害记录' }}</h3>
        <label v-for="field in templateFields" :key="field" class="form-item">
          <span>{{ field }}<em v-if="requiredFields.includes(field)">*</em></span>
          <input v-model="dialog.values[field]" :placeholder="`请输入${field}`" />
        </label>
        <p v-if="dialog.error" class="error-text">{{ dialog.error }}</p>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="submitDialog">保存</button>
          <button class="btn ghost" type="button" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type FacilityStat = Record<string, string | number>
type ImportFailure = { line: number; 病害编号: string; reasons: string[] }
type ImportResult = {
  ok: boolean
  message: string
  total: number
  imported: number
  failed: number
  failures: ImportFailure[]
}

const ENDPOINT = '/api/disease'
const columns = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员", "病害状态"]
const actions = ["确认定级", "提交闭环", "挂起病害"]
const statuses = ["待定级", "已定级", "处置中", "已闭环", "已挂起"]
const templateFields = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员"]
const requiredFields = ["病害编号", "所在设施", "病害类型"]
const allowedTypesText = "裂缝、坑槽、车辙、沉陷、松散、泛油、麻面、露筋、渗水、支座损坏、伸缩缝损坏、护栏损坏、排水不畅"
const stats = [{"label": "待定级病害", "value": 0}, {"label": "处置中病害", "value": 0}, {"label": "超期未闭环", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const facilityStats = ref<FacilityStat[]>([])
const importResult = ref<ImportResult | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dialog = reactive({
  visible: false,
  mode: 'create' as 'create' | 'edit',
  id: 0,
  values: {} as Record<string, string>,
  error: '',
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function downloadTemplate() {
  window.open(`${ENDPOINT}/import/template`, '_blank')
}

function triggerImport() {
  fileInput.value?.click()
}

function openCreate() {
  dialog.mode = 'create'
  dialog.id = 0
  dialog.values = Object.fromEntries(templateFields.map((field) => [field, '']))
  dialog.error = ''
  dialog.visible = true
}

function openEdit(row: Row) {
  dialog.mode = 'edit'
  dialog.id = Number(row.id)
  dialog.values = Object.fromEntries(templateFields.map((field) => [field, String(row[field] ?? '')]))
  dialog.error = ''
  dialog.visible = true
}

function closeDialog() {
  dialog.visible = false
}

async function readCsvText(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const utf8 = new TextDecoder('utf-8').decode(buffer)
  if (!utf8.includes('�')) {
    return utf8
  }
  try {
    return new TextDecoder('gbk').decode(buffer)
  } catch {
    return utf8
  }
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  errorMessage.value = ''
  try {
    const content = await readCsvText(file)
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, content }),
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '批量导入接口调用失败')
    }
    importResult.value = payload as ImportResult
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量导入失败'
  }
}

async function submitDialog() {
  dialog.error = ''
  const isCreate = dialog.mode === 'create'
  const url = isCreate ? ENDPOINT : `${ENDPOINT}/${dialog.id}`
  try {
    const response = await request(url, {
      method: isCreate ? 'POST' : 'PUT',
      body: JSON.stringify({ values: dialog.values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '保存失败')
    }
    closeDialog()
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    dialog.error = error instanceof Error ? error.message : '保存失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '病害登记动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '病害登记操作失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      throw new Error('台账统计读取失败')
    }
    const payload = await response.json()
    facilityStats.value = payload.items ?? []
  } catch {
    facilityStats.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('病害记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '病害登记列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.panel-title { margin: 0 0 8px; font-size: 14px; }
.panel-desc { font-size: 13px; margin: 4px 0 8px; }
.panel-desc.muted { color: var(--muted); font-size: 12px; }
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.4); display: flex; align-items: center; justify-content: center; z-index: 10; }
.modal { background: #fff; border-radius: 8px; padding: 16px; width: 420px; max-height: 80vh; overflow: auto; }
.form-item { display: block; margin-bottom: 10px; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.form-item input { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
</style>

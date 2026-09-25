<template>
  <section class="page" data-module="disease">
    <header class="page-head">
      <div>
        <h2>病害登记管理</h2>
        <p class="page-desc">维护病害记录，围绕病害编号、所在设施、病害类型、病害位置做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记病害记录</button>
        <button class="btn" type="button" @click="toggleImport">批量导入病害记录</button>
        <button class="btn" type="button" @click="downloadTemplate">下载导入模板</button>
        <button class="btn" type="button" @click="exportRows">导出病害登记清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section v-if="showImport" class="import-panel">
      <h3 class="panel-title">批量导入病害记录</h3>
      <p class="panel-desc">
        按固定模板填写：{{ templateFields.join('、') }}。病害状态由系统置为「待定级」。
        病害类型限：{{ allowedTypes.join('、') }}。
        所在设施需与道路、桥梁、隧道台账中的编码或名称一致；有问题的行不会落库，会逐条说明原因。
      </p>
      <div class="import-inputs">
        <input type="file" accept=".csv,text/csv" @change="onFilePicked" />
        <textarea
          v-model="importContent"
          rows="6"
          placeholder="选择模板 CSV 文件，或直接把模板内容粘贴到这里"
        ></textarea>
      </div>
      <div class="import-actions">
        <button class="btn primary" type="button" :disabled="importing" @click="submitImport">
          {{ importing ? '导入中…' : '开始导入' }}
        </button>
        <button class="btn ghost" type="button" @click="toggleImport">收起</button>
      </div>
      <div v-if="importResult" class="import-result">
        <p :class="importResult.failed.length ? 'error-text' : 'ok-text'">{{ importResult.message }}</p>
        <table v-if="importResult.imported.length" class="data-table">
          <thead>
            <tr><th>已登记病害编号</th><th>所在设施</th><th>病害类型</th></tr>
          </thead>
          <tbody>
            <tr v-for="entry in importResult.imported" :key="String(entry.id)">
              <td>{{ entry['病害编号'] }}</td>
              <td>{{ entry['所在设施'] }}</td>
              <td>{{ entry['病害类型'] }}</td>
            </tr>
          </tbody>
        </table>
        <table v-if="importResult.failed.length" class="data-table">
          <thead>
            <tr><th>行号</th><th>病害编号</th><th>未落库原因</th></tr>
          </thead>
          <tbody>
            <tr v-for="failure in importResult.failed" :key="failure.line">
              <td>{{ failure.line }}</td>
              <td>{{ failure.key }}</td>
              <td class="error-text">{{ failure.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="ledger-panel">
      <h3 class="panel-title">病害台账 · 按所在设施统计</h3>
      <table class="data-table">
        <thead>
          <tr><th>所在设施</th><th>病害数量</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in facilityStats" :key="item['所在设施']">
            <td>{{ item['所在设施'] }}</td>
            <td>{{ item['数量'] }}</td>
          </tr>
          <tr v-if="!facilityStats.length">
            <td colspan="2" class="empty-state">暂无病害记录，导入或登记后自动汇总</td>
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
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ImportFailure = { line: number; key: string; reason: string }
type ImportResult = {
  ok: boolean
  message: string
  imported: Row[]
  failed: ImportFailure[]
  duplicated: boolean
}

const ENDPOINT = '/api/disease'
const columns = ["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员", "病害状态"]
const actions = ["确认定级", "提交闭环", "挂起病害"]
const statuses = ["待定级", "已定级", "处置中", "已闭环", "已挂起"]
const stats = [{"label": "待定级病害", "value": 0}, {"label": "处置中病害", "value": 0}, {"label": "超期未闭环", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const showImport = ref(false)
const importing = ref(false)
const importContent = ref('')
const importSource = ref('')
const importResult = ref<ImportResult | null>(null)
const templateFields = ref<string[]>(["病害编号", "所在设施", "病害类型", "病害位置", "严重等级", "发现日期", "登记人员"])
const allowedTypes = ref<string[]>([])
const facilityStats = ref<Record<string, string | number>[]>([])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '病害记录登记入口尚未接入审批流'
}

function toggleImport() {
  showImport.value = !showImport.value
  if (showImport.value) {
    void loadTemplateMeta()
  }
}

async function loadTemplateMeta() {
  try {
    const response = await request(`${ENDPOINT}/import/template`)
    if (!response.ok) return
    const payload = await response.json()
    templateFields.value = payload.fields ?? templateFields.value
    allowedTypes.value = payload.allowed_types ?? []
  } catch {
    // 模板说明拉取失败不阻断导入，页面上保留默认字段提示
  }
}

async function downloadTemplate() {
  try {
    const response = await request(`${ENDPOINT}/import/template`)
    if (!response.ok) {
      throw new Error('导入模板下载失败，请稍后重试')
    }
    const payload = await response.json()
    const blob = new Blob(['\ufeff' + payload.content], { type: 'text/csv;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = payload.filename ?? '病害记录导入模板.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入模板下载失败'
  }
}

function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importSource.value = file.name
  const reader = new FileReader()
  reader.onload = () => {
    importContent.value = String(reader.result ?? '')
  }
  reader.readAsText(file, 'utf-8')
}

async function submitImport() {
  errorMessage.value = ''
  importResult.value = null
  if (!importContent.value.trim()) {
    errorMessage.value = '请先选择模板文件或粘贴模板内容'
    return
  }
  importing.value = true
  try {
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ content: importContent.value, source: importSource.value || null }),
    })
    if (!response.ok) {
      throw new Error('批量导入请求未生效，请稍后重试')
    }
    importResult.value = (await response.json()) as ImportResult
    await Promise.all([reload(), loadFacilityStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量导入失败'
  } finally {
    importing.value = false
  }
}

async function loadFacilityStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const payload = await response.json()
    facilityStats.value = payload.items ?? []
  } catch {
    // 台账统计刷新失败时保留旧数据，主列表错误单独提示
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('病害登记动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '病害登记操作失败'
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
  void loadFacilityStats()
})
</script>

<style scoped>
.import-panel,
.ledger-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.panel-title {
  font-size: 14px;
  margin: 0 0 8px;
}
.panel-desc {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 10px;
  line-height: 1.6;
}
.import-inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.import-inputs textarea {
  width: 100%;
  font-family: monospace;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
}
.import-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.import-result .data-table {
  margin-top: 8px;
}
.ok-text {
  color: #15803d;
}
</style>

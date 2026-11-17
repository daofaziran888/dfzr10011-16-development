<template>
  <section class="page" data-module="datasource">
    <header class="page-head">
      <div>
        <h2>采集数据源池管理</h2>
        <p class="page-desc">维护采集数据源的协议配置、优先级排序与接入管理，为采集数据源池提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记数据源条目</button>
        <button class="btn" type="button" @click="exportRows">导出采集数据源池清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

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
          <td :colspan="columns.length + 1" class="empty-state">暂无采集数据源池数据，可先登记数据源条目</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集数据源池记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="chain-section">
      <h3>模板发布链路</h3>
      <p class="page-desc">
        配置模板 + 配置参数 + 数据源协议 → 校验参数阈值与字段条件 → 配置快照 → 模板发布版本（评审待处理队列）→ 数据源引用清单。
        判定标准覆盖条件、范围和上限，任一不满足都不允许保存。
      </p>

      <div class="stat-row">
        <article v-for="item in chainStats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <h4>配置快照</h4>
      <form class="filter-bar" @submit.prevent="createSnapshot">
        <label class="filter-item">
          <span>模板编号</span>
          <input v-model="snapshotForm.模板编号" placeholder="CRAW-0001" />
        </label>
        <label class="filter-item">
          <span>条目编号</span>
          <input v-model="snapshotForm.条目编号" placeholder="DATA-0001" />
        </label>
        <label class="filter-item">
          <span>快照类型</span>
          <select v-model="snapshotForm.快照类型">
            <option>全量</option>
            <option>差量</option>
          </select>
        </label>
        <label class="filter-item">
          <span>协议范围</span>
          <input v-model="snapshotForm.协议范围" placeholder="HTTP,HTTPS" />
        </label>
        <label class="filter-item">
          <span>配置参数（JSON）</span>
          <input v-model="snapshotForm.配置参数" placeholder='{"并发数": 8, "超时秒": 30, "重试次数": 3}' />
        </label>
        <button class="btn primary" type="submit">生成配置快照</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in snapshotColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in snapshots" :key="String(row.快照编号)">
            <td v-for="column in snapshotColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <template v-if="row.status === '待评审'">
                <button class="link" type="button" @click="reviewSnapshot(row, '通过')">通过评审</button>
                <button class="link" type="button" @click="reviewSnapshot(row, '驳回')">驳回</button>
              </template>
              <span v-else>—</span>
            </td>
          </tr>
          <tr v-if="!snapshots.length">
            <td :colspan="snapshotColumns.length + 1" class="empty-state">暂无配置快照，可先生成配置快照</td>
          </tr>
        </tbody>
      </table>

      <h4>模板发布版本（评审待处理队列 {{ queueTotal }} 条）</h4>
      <form class="filter-bar" @submit.prevent="createVersion">
        <label class="filter-item">
          <span>模板编号</span>
          <input v-model="versionForm.模板编号" placeholder="CRAW-0001" />
        </label>
        <label class="filter-item">
          <span>条目编号</span>
          <input v-model="versionForm.条目编号" placeholder="DATA-0001" />
        </label>
        <label class="filter-item">
          <span>快照编号</span>
          <input v-model="versionForm.快照编号" placeholder="SNAP-0004（需已通过评审）" />
        </label>
        <label class="filter-item">
          <span>发布方式</span>
          <select v-model="versionForm.发布方式">
            <option>全量发布</option>
            <option>差量复核</option>
          </select>
        </label>
        <button class="btn primary" type="submit">创建发布版本</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in versionColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in versions" :key="String(row.版本编号)">
            <td v-for="column in versionColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <template v-if="row.status === '待评审'">
                <button class="link" type="button" @click="reviewVersion(row, '通过')">评审通过</button>
                <button class="link" type="button" @click="reviewVersion(row, '驳回')">评审驳回</button>
              </template>
              <span v-else>—</span>
            </td>
          </tr>
          <tr v-if="!versions.length">
            <td :colspan="versionColumns.length + 1" class="empty-state">暂无模板发布版本，可先创建发布版本</td>
          </tr>
        </tbody>
      </table>

      <h4>模板引用清单</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in referenceColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in references" :key="String(row.清单编号)">
            <td v-for="column in referenceColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="reconcile(row)">核对</button>
              <button class="link" type="button" @click="rollback(row)">回退</button>
              <button class="link" type="button" @click="showTasks(row)">任务快照</button>
            </td>
          </tr>
          <tr v-if="!references.length">
            <td :colspan="referenceColumns.length + 1" class="empty-state">暂无模板引用清单，发布版本评审通过后自动生成</td>
          </tr>
        </tbody>
      </table>

      <pre v-if="inspectText" class="inspect-box">{{ inspectText }}</pre>

      <footer class="page-foot">
        <span v-if="chainMessage" class="ok-text">{{ chainMessage }}</span>
        <span v-if="chainError" class="error-text">{{ chainError }}</span>
      </footer>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/datasource'
const columns = ["条目编号", "数据源名称", "源类型", "接入协议", "认证方式", "优先级", "最近采集", "条目状态"]
const actions = ["接入源", "暂停源", "移除源"]
const statuses = ["草稿", "已接入", "已暂停", "已移除"]
const stats = [{"label": "数据源条目总数", "value": 0}, {"label": "已接入源", "value": 0}, {"label": "已暂停源", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const snapshotColumns = ["快照编号", "模板编号", "条目编号", "快照类型", "协议范围", "校验结论", "status", "创建时间"]
const versionColumns = ["版本编号", "模板编号", "条目编号", "绑定快照", "发布方式", "回退点快照", "评审结论", "status"]
const referenceColumns = ["清单编号", "条目编号", "模板编号", "当前发布版本", "当前快照", "回退点快照", "更新方式", "最近切换条目", "核对结果"]

const snapshots = ref<Row[]>([])
const versions = ref<Row[]>([])
const references = ref<Row[]>([])
const queueTotal = ref(0)
const chainStats = ref<{ label: string; value: string | number }[]>([])
const chainMessage = ref('')
const chainError = ref('')
const inspectText = ref('')

const snapshotForm = ref({
  模板编号: 'CRAW-0001',
  条目编号: 'DATA-0001',
  快照类型: '全量',
  协议范围: 'HTTP,HTTPS',
  配置参数: '{"并发数": 8, "超时秒": 30, "重试次数": 3}',
})
const versionForm = ref({
  模板编号: 'CRAW-0001',
  条目编号: 'DATA-0001',
  快照编号: '',
  发布方式: '全量发布',
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '数据源条目登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集数据源池动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集数据源池操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('数据源条目列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据源条目列表读取失败'
  }
}

async function postChain(path: string, values: Record<string, unknown>) {
  chainError.value = ''
  chainMessage.value = ''
  try {
    const response = await request(path, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? payload.detail ?? '发布链路操作未生效')
    }
    chainMessage.value = payload.message ?? '发布链路操作已完成'
    await reloadChain()
  } catch (error) {
    chainError.value = error instanceof Error ? error.message : '发布链路操作失败'
  }
}

function createSnapshot() {
  inspectText.value = ''
  void postChain(`${ENDPOINT}/release/snapshots`, { ...snapshotForm.value })
}

function reviewSnapshot(row: Row, decision: string) {
  inspectText.value = ''
  void postChain(`${ENDPOINT}/release/snapshots/${row.快照编号}/review`, { 结论: decision })
}

function createVersion() {
  inspectText.value = ''
  void postChain(`${ENDPOINT}/release/versions`, { ...versionForm.value })
}

function reviewVersion(row: Row, decision: string) {
  inspectText.value = ''
  void postChain(`${ENDPOINT}/release/versions/${row.版本编号}/review`, { 结论: decision })
}

function rollback(row: Row) {
  inspectText.value = ''
  void postChain(`${ENDPOINT}/release/references/${row.清单编号}/rollback`, {})
}

async function showInspect(path: string) {
  chainError.value = ''
  chainMessage.value = ''
  try {
    const response = await request(path)
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '读取失败')
    }
    inspectText.value = JSON.stringify(payload, null, 2)
    await reloadChain()
  } catch (error) {
    chainError.value = error instanceof Error ? error.message : '读取失败'
  }
}

function reconcile(row: Row) {
  void showInspect(`${ENDPOINT}/release/references/${row.清单编号}/reconcile`)
}

function showTasks(row: Row) {
  void showInspect(`${ENDPOINT}/release/references/${row.清单编号}/tasks`)
}

async function reloadChain() {
  const [summaryRes, snapshotsRes, versionsRes, referencesRes, queueRes] = await Promise.all([
    request(`${ENDPOINT}/release/summary`),
    request(`${ENDPOINT}/release/snapshots?size=50`),
    request(`${ENDPOINT}/release/versions?size=50`),
    request(`${ENDPOINT}/release/references?size=50`),
    request(`${ENDPOINT}/release/queue`),
  ])
  const summary = await summaryRes.json()
  const snapshotsPayload = await snapshotsRes.json()
  const versionsPayload = await versionsRes.json()
  const referencesPayload = await referencesRes.json()
  const queuePayload = await queueRes.json()
  snapshots.value = snapshotsPayload.items ?? []
  versions.value = versionsPayload.items ?? []
  references.value = referencesPayload.items ?? []
  queueTotal.value = queuePayload.total ?? 0
  chainStats.value = [
    { label: '待评审快照', value: summary.配置快照?.待评审 ?? 0 },
    { label: '评审待处理队列', value: summary.评审待处理队列 ?? 0 },
    { label: '已发布版本', value: summary.模板发布版本?.已发布 ?? 0 },
    { label: '引用清单一致/异常', value: `${summary.模板引用清单?.一致 ?? 0}/${summary.模板引用清单?.异常 ?? 0}` },
  ]
}

onMounted(() => {
  void reload()
  void reloadChain()
})
</script>

<template>
  <section class="page" data-module="dashboard">
    <header class="page-head">
      <div>
        <h2>全局采集看板管理</h2>
        <p class="page-desc">维护全局看板的指标配置、刷新策略与告警联动，为全局采集看板提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记看板面板</button>
        <button class="btn" type="button" @click="exportRows">导出全局采集看板清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无全局采集看板数据，可先登记看板面板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条全局采集看板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="chain-section">
      <h3>模板发布规则评审汇总</h3>
      <p class="page-desc">同步取到数据源模块的模板发布规则评审汇总和模板发布版本数据。</p>
      <div class="stat-row">
        <article v-for="item in releaseStats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in releaseColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in releaseVersions" :key="String(row.版本编号)">
            <td v-for="column in releaseColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!releaseVersions.length">
            <td :colspan="releaseColumns.length" class="empty-state">暂无模板发布版本数据</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dashboard'
const columns = ["面板编号", "面板名称", "显示指标", "刷新间隔", "告警配置", "数据范围", "创建人", "面板状态"]
const actions = ["启用面板", "暂停面板", "归档面板"]
const statuses = ["草稿", "已启用", "已暂停", "已归档"]
const stats = [{"label": "看板面板总数", "value": 0}, {"label": "已启用面板", "value": 0}, {"label": "已暂停面板", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const releaseColumns = ["版本编号", "模板编号", "条目编号", "绑定快照", "发布方式", "回退点快照", "status"]
const releaseStats = ref<{ label: string; value: string | number }[]>([])
const releaseVersions = ref<Row[]>([])

async function reloadReleaseChain() {
  try {
    const response = await request(`${ENDPOINT}/release-summary`)
    if (!response.ok) {
      throw new Error('模板发布评审汇总读取失败')
    }
    const payload = await response.json()
    const summary = payload.summary ?? {}
    releaseStats.value = [
      { label: '待评审快照', value: summary.配置快照?.待评审 ?? 0 },
      { label: '评审待处理队列', value: summary.评审待处理队列 ?? 0 },
      { label: '已发布版本', value: summary.模板发布版本?.已发布 ?? 0 },
      { label: '引用清单一致/异常', value: `${summary.模板引用清单?.一致 ?? 0}/${summary.模板引用清单?.异常 ?? 0}` },
    ]
    releaseVersions.value = payload.versions ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板发布评审汇总读取失败'
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '看板面板登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('全局采集看板动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全局采集看板操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('看板面板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全局采集看板列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadReleaseChain()
})
</script>

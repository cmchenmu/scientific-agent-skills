import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

const api = import.meta.env.VITE_API_URL ?? ''
const project = 'mouse-neuro-demo'

type Citation = { document_id: string; chunk_id: string; title: string; section?: string; page?: number }
type Answer = { answer: string; status: string; citations: Citation[]; confidence: number }
type Task = { id: string; state: string; request_type: string }
type LibrarySummary = { documents: number; chunks: number }
type LiteratureImport = { query: string; requested: number; downloaded: number; created: number; duplicates: number; direction?: string }

function request(path: string, user: string, options: RequestInit = {}) {
  return fetch(`${api}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-User-Id': user, ...(options.headers ?? {}) },
  })
}

function App() {
  const [user, setUser] = useState('research-demo')
  const [question, setQuestion] = useState('组织固定前应如何保存？')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [message, setMessage] = useState('')
  const [tasks, setTasks] = useState<Task[]>([])
  const [amount, setAmount] = useState('1200')
  const [description, setDescription] = useState('演示出租车费')
  const [actionId, setActionId] = useState<string | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID())
  const [researchDirection, setResearchDirection] = useState('小鼠海马神经发生与阿尔茨海默病')
  const [literatureCount, setLiteratureCount] = useState('100')
  const [library, setLibrary] = useState<LibrarySummary | null>(null)
  const [importing, setImporting] = useState(false)

  const loadTasks = async () => {
    const response = await request('/v1/tasks', user)
    if (response.ok) setTasks(await response.json() as Task[])
  }

  const loadLibrary = async () => {
    const response = await request(`/v1/library/summary?project_id=${encodeURIComponent(project)}`, user)
    if (response.ok) setLibrary(await response.json() as LibrarySummary)
  }

  useEffect(() => { void loadTasks(); void loadLibrary() }, [user])

  const ask = async () => {
    setMessage('正在检索已授权知识库...')
    const response = await request('/v1/knowledge/query', user, {
      method: 'POST', body: JSON.stringify({ project_id: project, question }),
    })
    if (!response.ok) { setMessage(await response.text()); return }
    setAnswer(await response.json() as Answer)
    setMessage('')
  }

  const createAction = async () => {
    setMessage('')
    const response = await request('/v1/actions/reimbursements', user, {
      method: 'POST', body: JSON.stringify({
        idempotency_key: idempotencyKey,
        payload: { project_id: project, amount_cents: Number(amount), currency: 'CNY', description, receipts: ['demo-receipt-001'] },
      }),
    })
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as { action_id: string }
    setActionId(result.action_id)
    setMessage(`已创建待审批任务：${result.action_id}`)
    await loadTasks()
  }

  const approveAndConfirm = async () => {
    if (!actionId) return
    const approval = await request(`/v1/actions/${actionId}/approve`, 'finance-demo', {
      method: 'POST', body: JSON.stringify({ decision: 'approved' }),
    })
    if (!approval.ok) { setMessage(await approval.text()); return }
    const submit = await request(`/v1/actions/${actionId}/confirm`, user, {
      method: 'POST', headers: { 'X-Idempotency-Key': idempotencyKey },
    })
    if (submit.ok) {
      setMessage(`模拟提交成功：${(await submit.json() as { external_id: string }).external_id}`)
      setActionId(null)
      setIdempotencyKey(crypto.randomUUID())
    } else setMessage(await submit.text())
    await loadTasks()
  }

  const importResearchDirection = async () => {
    setImporting(true)
    setMessage('正在从 Europe PMC 下载开放获取全文并建立本地索引；100 篇可能需要数分钟。')
    const response = await request('/v1/literature/direction-import', user, {
      method: 'POST', body: JSON.stringify({ project_id: project, direction: researchDirection, limit: Number(literatureCount) }),
    })
    setImporting(false)
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as LiteratureImport
    setMessage(`文献入库完成：检索 ${result.query}；下载 ${result.downloaded} 篇，新增 ${result.created} 篇，重复 ${result.duplicates} 篇。`)
    await loadLibrary()
  }

  return <main style={{ maxWidth: 980, margin: '32px auto', fontFamily: 'system-ui, sans-serif', color: '#172033', padding: '0 20px' }}>
    <header style={{ borderBottom: '1px solid #d9e0e8', paddingBottom: 18 }}>
      <h1 style={{ margin: 0 }}>Lab Agent</h1>
      <p style={{ marginBottom: 0 }}>本地开发演示：ACL 知识问答、论文工作流基础能力与受控行政审批。</p>
    </header>
    <section style={sectionStyle}>
      <label>开发身份 <select value={user} onChange={(event) => setUser(event.target.value)}>
        <option value="student-demo">Student Demo</option><option value="research-demo">Research Assistant Demo</option><option value="pi-demo">PI Demo</option>
      </select></label>
      <span style={{ marginLeft: 14 }}>项目：{project}</span>
    </section>
    <section style={sectionStyle}>
      <h2>知识问答</h2>
      <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} style={{ width: '100%', boxSizing: 'border-box' }} />
      <button onClick={() => void ask()} style={buttonStyle}>检索已授权证据</button>
      {answer && <div style={resultStyle}><strong>{answer.status}</strong><p>{answer.answer}</p>{answer.citations.map((citation) => <p key={citation.chunk_id}><small>引用：{citation.title} · {citation.section ?? `第 ${citation.page} 页`} · {citation.document_id}</small></p>)}</div>}
    </section>
    <section style={sectionStyle}>
      <h2>研究方向助手</h2>
      <p>{library ? `当前项目：${library.documents} 篇文档，${library.chunks} 个可检索片段。` : '正在读取知识库统计...'}</p>
      <label>科研方向 <textarea value={researchDirection} onChange={(event) => setResearchDirection(event.target.value)} rows={3} style={{ width: '100%', boxSizing: 'border-box' }} /></label>
      <label>论文数量 <select value={literatureCount} onChange={(event) => setLiteratureCount(event.target.value)}><option value="50">50 篇</option><option value="100">100 篇</option></select></label>
      <p><button onClick={() => void importResearchDirection()} disabled={importing} style={buttonStyle}>{importing ? '正在检索并入库...' : '按研究方向扩充知识库'}</button></p>
      <small>系统将方向转为可审计的 Europe PMC 查询，只导入开放获取 PMC 全文；Research Assistant 或 PI 演示身份可以执行。</small>
    </section>
    <section style={sectionStyle}>
      <h2>模拟报销</h2>
      <label>金额（分） <input value={amount} onChange={(event) => setAmount(event.target.value)} inputMode="numeric" /></label>
      <label style={{ marginLeft: 12 }}>说明 <input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <p><button onClick={() => void createAction()} style={buttonStyle}>创建预览并送审</button>{actionId && <button onClick={() => void approveAndConfirm()} style={{ ...buttonStyle, marginLeft: 8 }}>以演示财务审批并提交</button>}</p>
      <small>该操作仅写入本地 SQLite 模拟器，不连接真实财务系统。</small>
    </section>
    <section style={sectionStyle}><h2>我的任务</h2>{tasks.length ? <ul>{tasks.map((task) => <li key={task.id}>{task.request_type} · {task.state} · {task.id}</li>)}</ul> : <p>暂无任务。</p>}</section>
    {message && <p style={{ background: '#fff3cd', padding: 12 }}>{message}</p>}
  </main>
}

const sectionStyle = { borderBottom: '1px solid #d9e0e8', padding: '20px 0' }
const buttonStyle = { background: '#0f766e', color: '#fff', border: 0, borderRadius: 4, padding: '9px 13px', marginTop: 10, cursor: 'pointer' }
const resultStyle = { background: '#eef6ff', padding: 14, marginTop: 12 }

createRoot(document.getElementById('root')!).render(<App />)

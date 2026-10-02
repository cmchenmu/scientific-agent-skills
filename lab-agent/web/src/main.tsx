import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

const api = import.meta.env.VITE_API_URL ?? ''
const project = 'mouse-neuro-demo'

type Citation = { document_id: string; chunk_id: string; title: string; section?: string; page?: number }
type Answer = { answer: string; status: string; citations: Citation[]; confidence: number }
type Task = { id: string; state: string; request_type: string }

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

  const loadTasks = async () => {
    const response = await request('/v1/tasks', user)
    if (response.ok) setTasks(await response.json() as Task[])
  }

  useEffect(() => { void loadTasks() }, [user])

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

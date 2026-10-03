import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

const api = import.meta.env.VITE_API_URL ?? ''
const project = 'mouse-neuro-demo'

type Citation = { document_id: string; chunk_id: string; title: string; section?: string; page?: number }
type KeyInformation = { chunk_id: string; section?: string; page?: number; text: string }
type RelatedPaper = { document_id: string; title: string; key_information: KeyInformation[] }
type Answer = { answer: string; status: string; citations: Citation[]; related_papers: RelatedPaper[]; literature_query?: string | null; confidence: number }
type Task = { id: string; state: string; request_type: string }
type LibrarySummary = { documents: number; chunks: number }
type LiteratureImport = { query: string; requested: number; downloaded: number; created: number; duplicates: number; direction?: string }
type AgentRun = Answer & { candidate_query?: string; executed_tools: string[]; model_used: boolean }
type Paper = { document_id: string; title: string; score: number; snippet: string; section?: string }
type Evidence = { chunk_id: string; section?: string; page?: number; text: string }
type Automation = { method: string; script_filename: string; script: string; input_template_filename: string; input_template: string; input_specification: string }
type Experiment = { document_id: string; title: string; experimental_workflow: Evidence[]; materials_and_equipment: Evidence[]; data_analysis_methods: Evidence[]; expected_results: Evidence[]; conclusions: Evidence[]; automations: Automation[]; review_note: string }
type CatalogPaper = { pmcid: string; title: string; journal?: string; year?: string; authors?: string; abstract?: string; impact_factor?: number; impact_factor_year?: string; impact_factor_source?: string }
type CatalogPage = { total: number; papers: CatalogPaper[]; current_page: number; total_pages: number; display_mode?: string; page_displayed?: number }
type CatalogInspection = { pmcid: string; methods: { section: string; text: string }[]; technical_route: string[]; data_tables: { caption: string; data: string }[]; data_summary: string; figures: { caption: string; image_url?: string; source_url: string }[]; extraction_status: string; note: string }
type LibraryDocument = { document_id: string; title: string; effective_at: string; status: string; source_format: string; source: string }

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
  const [library, setLibrary] = useState<LibrarySummary | null>(null)
  const [importing, setImporting] = useState(false)
  const [candidateQuery, setCandidateQuery] = useState<string | null>(null)
  const [paperQuery, setPaperQuery] = useState('mouse hippocampus')
  const [papers, setPapers] = useState<Paper[]>([])
  const [papersLinkedFromKnowledge, setPapersLinkedFromKnowledge] = useState(false)
  const [experiment, setExperiment] = useState<Experiment | null>(null)
  const [catalogQuery, setCatalogQuery] = useState('mouse hippocampus')
  const [catalog, setCatalog] = useState<CatalogPaper[]>([])
  const [catalogTotal, setCatalogTotal] = useState(0)
  const [catalogCurrentPage, setCatalogCurrentPage] = useState(1)
  const [catalogTotalPages, setCatalogTotalPages] = useState(0)
  const [catalogPageInput, setCatalogPageInput] = useState('1')
  const [catalogAbstracts, setCatalogAbstracts] = useState<Record<string, string | null>>({})
  const [expandedAbstracts, setExpandedAbstracts] = useState<string[]>([])
  const [inspections, setInspections] = useState<Record<string, CatalogInspection>>({})
  const [inspectionErrors, setInspectionErrors] = useState<Record<string, string>>({})
  const [expandedInspections, setExpandedInspections] = useState<string[]>([])
  const [catalogSort, setCatalogSort] = useState('relevance')
  const [catalogDisplayMode, setCatalogDisplayMode] = useState('with_data_or_images')
  const [selectedPmcids, setSelectedPmcids] = useState<string[]>([])
  const [libraryDocuments, setLibraryDocuments] = useState<LibraryDocument[]>([])

  const loadTasks = async () => {
    const response = await request('/v1/tasks', user)
    if (response.ok) setTasks(await response.json() as Task[])
  }

  const loadLibrary = async () => {
    const response = await request(`/v1/library/summary?project_id=${encodeURIComponent(project)}`, user)
    if (response.ok) setLibrary(await response.json() as LibrarySummary)
  }

  const loadLibraryDocuments = async () => {
    const response = await request(`/v1/library/documents?project_id=${encodeURIComponent(project)}`, user)
    if (response.ok) setLibraryDocuments(await response.json() as LibraryDocument[])
  }

  useEffect(() => { void loadTasks(); void loadLibrary(); void loadLibraryDocuments() }, [user])

  const ask = async () => {
    setMessage('正在检索已授权知识库...')
    const response = await request('/v1/knowledge/query', user, {
      method: 'POST', body: JSON.stringify({ project_id: project, question }),
    })
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as Answer
    setAnswer(result)
    setPaperQuery(question)
    setPapers(result.related_papers.map((paper, index) => ({
      document_id: paper.document_id,
      title: paper.title,
      score: result.related_papers.length - index,
      snippet: paper.key_information.map((item) => item.text).join(' '),
      section: paper.key_information[0]?.section,
    })))
    setExperiment(null)
    setPapersLinkedFromKnowledge(result.related_papers.length > 0)
    if (result.literature_query) {
      setCatalogQuery(result.literature_query)
      setCatalogDisplayMode('with_data_or_images')
      await searchCatalog(result.literature_query, 1, 'relevance', 'with_data_or_images')
      setMessage(`已用关键词“${result.literature_query}”自动检索可下载论文，并将相关原文带入下方实验提取。`)
    } else setMessage(result.related_papers.length ? '已将相关论文和关键原文信息带入下方“论文实验提取”；未能生成开放论文检索关键词。' : '')
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

  const proposeLiteratureQuery = async () => {
    setMessage('正在在受限工具集合内生成候选检索式...')
    const response = await request('/v1/agent/run', user, {
      method: 'POST', body: JSON.stringify({ project_id: project, request: researchDirection, mode: 'literature_query' }),
    })
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as AgentRun
    setCandidateQuery(result.candidate_query ?? null)
    setMessage(result.candidate_query ? `候选检索式：${result.candidate_query}。请确认后再导入。` : result.answer)
  }

  const openCandidateCatalog = async () => {
    if (!candidateQuery?.trim()) return
    setCatalogQuery(candidateQuery)
    await searchCatalog(candidateQuery, 1)
    document.getElementById('catalog-search')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const searchPapers = async () => {
    setMessage('正在检索项目中已授权的论文...')
    const response = await request('/v1/literature/search', user, {
      method: 'POST', body: JSON.stringify({ project_id: project, query: paperQuery }),
    })
    if (!response.ok) { setMessage(await response.text()); return }
    setPapers(await response.json() as Paper[])
    setPapersLinkedFromKnowledge(false)
    setExperiment(null)
    setMessage('')
  }

  const selectPaper = async (paper: Paper) => {
    setMessage('正在从已授权全文抽取实验信息和可复用分析模板...')
    const response = await request(`/v1/literature/${paper.document_id}/experiment?project_id=${encodeURIComponent(project)}`, user)
    if (!response.ok) { setMessage(await response.text()); return }
    setExperiment(await response.json() as Experiment)
    setMessage('')
  }

  const download = (filename: string, content: string) => {
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }))
    link.download = filename
    link.click()
    URL.revokeObjectURL(link.href)
  }

  const searchCatalog = async (query = catalogQuery, page = 1, sortOrder = catalogSort, displayMode = catalogDisplayMode) => {
    setMessage('正在搜索 Europe PMC 中可下载的开放全文...')
    const response = await request('/v1/literature/catalog', user, { method: 'POST', body: JSON.stringify({ query, page, sort_order: sortOrder, display_mode: displayMode }) })
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as CatalogPage
    const orderedPage = orderCatalog(result.papers, sortOrder)
    setCatalog(orderedPage)
    setCatalogTotal(result.total)
    setCatalogCurrentPage(result.current_page)
    setCatalogTotalPages(result.total_pages)
    setCatalogPageInput(String(result.current_page))
    setSelectedPmcids([])
    setExpandedAbstracts([])
    setExpandedInspections([])
    setInspectionErrors({})
    setMessage(result.papers.length ? `搜索命中 ${result.total} 篇，当前第 ${result.current_page} / ${result.total_pages} 页。` : displayMode === 'with_data_or_images' ? '当前页没有可展示数据表或图片的论文，可切换为“显示全部论文”。' : '没有找到可下载的开放全文。')
  }

  const togglePmcid = (pmcid: string) => setSelectedPmcids((current) => {
    if (current.includes(pmcid)) return current.filter((item) => item !== pmcid)
    if (current.length >= 50) { setMessage('一次最多选择并下载 50 篇论文。'); return current }
    return [...current, pmcid]
  })

  const toggleAbstract = async (paper: CatalogPaper) => {
    if (expandedAbstracts.includes(paper.pmcid)) {
      setExpandedAbstracts((current) => current.filter((pmcid) => pmcid !== paper.pmcid))
      return
    }
    setExpandedAbstracts((current) => [...current, paper.pmcid])
    if (paper.abstract || paper.pmcid in catalogAbstracts) return
    const response = await request(`/v1/literature/catalog/${paper.pmcid}/abstract`, user)
    if (!response.ok) { setCatalogAbstracts((current) => ({ ...current, [paper.pmcid]: null })); return }
    const payload = await response.json() as { abstract: string | null }
    setCatalogAbstracts((current) => ({ ...current, [paper.pmcid]: payload.abstract }))
  }

  const toggleInspection = async (paper: CatalogPaper) => {
    if (expandedInspections.includes(paper.pmcid)) {
      setExpandedInspections((current) => current.filter((pmcid) => pmcid !== paper.pmcid))
      setInspectionErrors((current) => {
        const { [paper.pmcid]: _ignored, ...remaining } = current
        return remaining
      })
      return
    }
    setExpandedInspections((current) => [...current, paper.pmcid])
    if (inspections[paper.pmcid]) return
    const response = await request(`/v1/literature/catalog/${paper.pmcid}/inspection`, user)
    if (!response.ok) {
      const detail = await response.text()
      setInspectionErrors((current) => ({ ...current, [paper.pmcid]: `暂时无法读取：${detail}` }))
      return
    }
    const payload = await response.json() as CatalogInspection
    setInspections((current) => ({ ...current, [paper.pmcid]: payload }))
  }

  const goToCatalogPage = () => {
    const target = Number(catalogPageInput)
    if (Number.isInteger(target) && target >= 1 && target <= catalogTotalPages) void searchCatalog(catalogQuery, target)
    else setMessage(`请输入 1 到 ${catalogTotalPages} 之间的页码。`)
  }

  const downloadSelectedPapers = async (pmcids: string[]) => {
    if (!pmcids.length) return
    setImporting(true)
    setMessage(`正在下载并入库 ${pmcids.length} 篇已选择论文...`)
    const response = await request('/v1/literature/selected-import', user, { method: 'POST', body: JSON.stringify({ project_id: project, pmcids }) })
    setImporting(false)
    if (!response.ok) { setMessage(await response.text()); return }
    const result = await response.json() as LiteratureImport
    setMessage(`下载完成：${result.downloaded} 篇，新增 ${result.created} 篇，重复 ${result.duplicates} 篇。`)
    await loadLibrary()
    await loadLibraryDocuments()
  }

  const downloadOriginal = async (paper: LibraryDocument) => {
    const response = await request(`/v1/library/documents/${paper.document_id}/original?project_id=${encodeURIComponent(project)}`, user)
    if (!response.ok) { setMessage(await response.text()); return }
    const link = document.createElement('a')
    link.href = URL.createObjectURL(await response.blob())
    link.download = `${paper.title}.${paper.source_format.replace('.', '')}`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  const removeDocument = async (paper: LibraryDocument) => {
    if (!window.confirm(`删除《${paper.title}》及其本地原文副本？`)) return
    const response = await request(`/v1/library/documents/${paper.document_id}?project_id=${encodeURIComponent(project)}`, user, { method: 'DELETE' })
    if (!response.ok) { setMessage(await response.text()); return }
    setMessage(`已删除：${paper.title}`)
    await loadLibrary()
    await loadLibraryDocuments()
  }

  return <main style={{ maxWidth: 980, margin: '32px auto', fontFamily: 'system-ui, sans-serif', color: '#172033', padding: '0 20px' }}>
    <header style={{ borderBottom: '1px solid #d9e0e8', paddingBottom: 18 }}>
      <h1 style={{ margin: 0 }}>Lab Agent</h1>
      <p style={{ marginBottom: 0 }}>知识检索联动开放论文搜索、入库与实验信息提取；各模块也支持独立使用。</p>
    </header>
    <section style={sectionStyle}>
      <label>开发身份 <select value={user} onChange={(event) => setUser(event.target.value)}>
        <option value="student-demo">Student Demo</option><option value="research-demo">Research Assistant Demo</option><option value="pi-demo">PI Demo</option>
      </select></label>
      <span style={{ marginLeft: 14 }}>项目：{project}</span>
    </section>
    <section id="catalog-search" style={sectionStyle}>
      <h2>1. 知识检索与联动摘要</h2>
      <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} style={{ width: '100%', boxSizing: 'border-box' }} />
      <button onClick={() => void ask()} style={buttonStyle}>检索已授权证据</button>
      {answer && <div style={resultStyle}><strong>{answer.status}</strong><p>{answer.answer}</p>{answer.literature_query && <p><small>已自动用于论文检索的关键词：{answer.literature_query}</small></p>}<strong>出处</strong>{answer.citations.map((citation) => <p key={citation.chunk_id}><small>{citation.title} · {citation.section ?? `第 ${citation.page} 页`} · {citation.document_id}</small></p>)}</div>}
    </section>
    <section style={sectionStyle}>
      <h2>2. 开放论文检索与选择下载</h2>
      <p><small>知识检索完成后会自动带入关键词并搜索；也可在本模块独立输入关键词。默认优先显示可展示数据表或图片的论文，可切换为全部论文。</small></p>
      <label>搜索关键词 <input value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} style={{ minWidth: 280 }} /></label>
      <label style={{ marginLeft: 12 }}>排序 <select value={catalogSort} onChange={(event) => { setCatalogSort(event.target.value); void searchCatalog(catalogQuery, 1, event.target.value) }}><option value="relevance">相关性</option><option value="year_desc">发表年份（新到旧）</option><option value="impact_factor_desc">影响因子（高到低）</option></select></label>
      <label style={{ marginLeft: 12 }}>展示 <select value={catalogDisplayMode} onChange={(event) => { const mode = event.target.value; setCatalogDisplayMode(mode); void searchCatalog(catalogQuery, 1, catalogSort, mode) }}><option value="all">显示全部论文</option><option value="with_data_or_images">仅显示可展示数据或图片的论文</option></select></label>
      <button onClick={() => void searchCatalog()} style={{ ...buttonStyle, marginLeft: 8 }}>搜索可下载论文</button>
      {catalog.length > 0 && <div style={{ marginTop: 12 }}>
        <p>搜索命中 {catalogTotal} 篇；当前第 {catalogCurrentPage} / {catalogTotalPages} 页；{catalogDisplayMode === 'with_data_or_images' && `当前页符合数据/图片条件 ${catalog.length} 篇；`}已选择 {selectedPmcids.length} / 50 篇。<button onClick={() => void downloadSelectedPapers(catalog.map((paper) => paper.pmcid))} disabled={importing || !catalog.length} style={{ ...buttonStyle, marginLeft: 8 }}>{importing ? '正在下载...' : '下载当前页全部（最多20篇）'}</button>{selectedPmcids.length > 0 && <button onClick={() => void downloadSelectedPapers(selectedPmcids)} disabled={importing} style={{ ...buttonStyle, marginLeft: 8 }}>下载已选择论文</button>}</p>
        <div style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid #d9e0e8', padding: '0 10px' }}>{catalog.map((paper) => <div key={paper.pmcid} style={{ borderTop: '1px solid #d9e0e8', padding: '10px 0' }}><label><input type="checkbox" checked={selectedPmcids.includes(paper.pmcid)} onChange={() => togglePmcid(paper.pmcid)} /> <strong>{paper.title}</strong></label><br /><small>{paper.pmcid} · {[paper.authors, paper.journal, paper.year].filter(Boolean).join(' · ')} · 影响因子：{paper.impact_factor !== undefined && paper.impact_factor !== null ? `${paper.impact_factor}（${paper.impact_factor_year}）` : '未导入 JIF'}</small><p style={{ margin: '6px 0' }}><button onClick={() => void toggleAbstract(paper)} style={{ ...buttonStyle, marginTop: 0 }}>{expandedAbstracts.includes(paper.pmcid) ? '收起摘要' : '显示摘要'}</button><button onClick={() => void toggleInspection(paper)} style={{ ...buttonStyle, marginTop: 0, marginLeft: 8 }}>{expandedInspections.includes(paper.pmcid) ? '收起研究方法与数据' : '查看研究方法与数据'}</button></p>{expandedAbstracts.includes(paper.pmcid) && <p style={{ margin: '6px 0' }}><small>摘要：{paper.abstract ?? (paper.pmcid in catalogAbstracts ? catalogAbstracts[paper.pmcid] ?? '来源未提供摘要。' : '正在加载摘要...')}</small></p>}{expandedInspections.includes(paper.pmcid) && <InspectionView inspection={inspections[paper.pmcid]} error={inspectionErrors[paper.pmcid]} />}<button onClick={() => void downloadSelectedPapers([paper.pmcid])} disabled={importing} style={{ ...buttonStyle, marginLeft: 8 }}>下载此篇</button></div>)}</div>
        <p><button onClick={() => void searchCatalog(catalogQuery, 1)} disabled={catalogCurrentPage <= 1} style={buttonStyle}>首页</button><button onClick={() => void searchCatalog(catalogQuery, catalogCurrentPage - 1)} disabled={catalogCurrentPage <= 1} style={{ ...buttonStyle, marginLeft: 8 }}>上一页</button>{pageWindow(catalogCurrentPage, catalogTotalPages)[0] > 1 && <span style={{ marginLeft: 8 }}>...</span>}{pageWindow(catalogCurrentPage, catalogTotalPages).map((page) => <button key={page} onClick={() => void searchCatalog(catalogQuery, page)} disabled={page === catalogCurrentPage} style={{ ...buttonStyle, marginLeft: 8, background: page === catalogCurrentPage ? '#475569' : '#0f766e' }}>{page}</button>)}{pageWindow(catalogCurrentPage, catalogTotalPages).at(-1)! < catalogTotalPages && <span style={{ marginLeft: 8 }}>...</span>}<button onClick={() => void searchCatalog(catalogQuery, catalogCurrentPage + 1)} disabled={catalogCurrentPage >= catalogTotalPages} style={{ ...buttonStyle, marginLeft: 8 }}>下一页</button><button onClick={() => void searchCatalog(catalogQuery, catalogTotalPages)} disabled={catalogCurrentPage >= catalogTotalPages} style={{ ...buttonStyle, marginLeft: 8 }}>末页</button></p>
        <label>跳转到第 <input value={catalogPageInput} onChange={(event) => setCatalogPageInput(event.target.value)} inputMode="numeric" style={{ width: 70, margin: '0 6px' }} /> 页</label><button onClick={goToCatalogPage} style={{ ...buttonStyle, marginLeft: 8 }}>跳转</button>
      </div>}
    </section>
    <section style={sectionStyle}>
      <h2>3. 我的论文库</h2>
      <p>{libraryDocuments.length ? `已授权 ${libraryDocuments.length} 篇论文。` : '暂无可访问的已下载论文。'}</p>
      {libraryDocuments.length > 0 && <div style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid #d9e0e8', padding: '0 10px' }}>{libraryDocuments.map((paper) => <div key={paper.document_id} style={{ borderTop: '1px solid #d9e0e8', padding: '10px 0' }}><strong>{paper.title}</strong><br /><small>来源：{paper.source} · 入库：{paper.effective_at} · 状态：{paper.status} · 格式：{paper.source_format}</small><br /><button onClick={() => void downloadOriginal(paper)} style={buttonStyle}>下载原文</button>{user !== 'student-demo' && <button onClick={() => void removeDocument(paper)} style={{ ...buttonStyle, marginLeft: 8, background: '#b42318' }}>删除</button>}</div>)}</div>}
    </section>
    <section id="paper-extraction" style={sectionStyle}>
      <h2>4. 已入库论文实验提取</h2>
      <p><small>知识检索命中的本地论文会自动带入；也可在本模块独立检索已入库论文。</small></p>
      <label>关键词 <input value={paperQuery} onChange={(event) => setPaperQuery(event.target.value)} style={{ minWidth: 280 }} /></label>
      <button onClick={() => void searchPapers()} style={{ ...buttonStyle, marginLeft: 8 }}>检索前五篇论文</button>
      {papersLinkedFromKnowledge && <p><small>以下论文由上方知识检索自动联动；每篇卡片展示该次检索命中的关键原文信息。</small></p>}
      {papers.length > 0 && <div style={{ marginTop: 12 }}>{papers.map((paper) => <button key={paper.document_id} onClick={() => void selectPaper(paper)} style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 7, padding: 10, border: '1px solid #cbd5e1', background: '#fff', borderRadius: 4, cursor: 'pointer' }}><strong>{paper.title}</strong><br /><small>{paper.section ?? '正文'} · {paper.snippet}</small></button>)}</div>}
      {experiment && <div style={resultStyle}>
        <h3 style={{ marginTop: 0 }}>{experiment.title}</h3>
        <EvidenceGroup title="实验流程" items={experiment.experimental_workflow} />
        <EvidenceGroup title="材料与设备" items={experiment.materials_and_equipment} />
        <EvidenceGroup title="数据分析方法" items={experiment.data_analysis_methods} />
        <EvidenceGroup title="预期结果" items={experiment.expected_results} />
        <EvidenceGroup title="结论" items={experiment.conclusions} />
        <h3>自动化分析材料</h3>
        {experiment.automations.length ? experiment.automations.map((item) => <div key={item.script_filename} style={{ borderTop: '1px solid #cbd5e1', paddingTop: 10 }}><strong>{item.method}</strong><p><small>{item.input_specification}</small></p><button onClick={() => download(item.script_filename, item.script)} style={buttonStyle}>下载分析脚本</button><button onClick={() => download(item.input_template_filename, item.input_template)} style={{ ...buttonStyle, marginLeft: 8 }}>下载 CSV 模板</button></div>) : <p>未识别到受支持的分析方法，需研究人员根据原文选择方法和输入格式。</p>}
        <p><small>{experiment.review_note}</small></p>
      </div>}
    </section>
    <section style={sectionStyle}>
      <h2>研究方向助手</h2>
      <p>将科研方向转换为 Europe PMC 候选检索关键词，帮助从研究主题进入开放论文搜索。</p>
      <p>{library ? `当前项目：${library.documents} 篇文档，${library.chunks} 个可检索片段。` : '正在读取知识库统计...'}</p>
      <label>科研方向 <textarea value={researchDirection} onChange={(event) => setResearchDirection(event.target.value)} rows={3} style={{ width: '100%', boxSizing: 'border-box' }} /></label>
      <p><button onClick={() => void proposeLiteratureQuery()} style={buttonStyle}>生成候选检索式</button>{candidateQuery && <button onClick={() => void openCandidateCatalog()} style={{ ...buttonStyle, marginLeft: 8 }}>查看可下载论文</button>} {candidateQuery && <small>点击后将在“开放论文检索与选择下载”区域搜索并自动跳转。</small>}</p>
      {candidateQuery && <label>候选检索式（可编辑、复制）<textarea value={candidateQuery} onChange={(event) => setCandidateQuery(event.target.value)} rows={2} style={{ width: '100%', boxSizing: 'border-box', marginTop: 6 }} /></label>}
      <p><small>使用方式：输入研究对象、疾病或现象、物种、模型等信息，生成候选检索式后检查关键词，再点击“查看可下载论文”。</small></p>
      <small>该功能只生成候选查询，不会自动下载或入库。论文下载是独立操作，只显示开放获取 PMC 全文，且需要 Research Assistant 或 PI 身份。</small>
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

function EvidenceGroup({ title, items }: { title: string; items: Evidence[] }) {
  return <div><h3>{title}</h3>{items.length ? <ul>{items.map((item) => <li key={item.chunk_id}><small>{item.section ?? (item.page ? `第 ${item.page} 页` : '正文')}：</small>{item.text}</li>)}</ul> : <p>未在可读取正文中定位到对应证据。</p>}</div>
}

function InspectionView({ inspection, error }: { inspection?: CatalogInspection; error?: string }) {
  if (error) return <p><small>{error}</small></p>
  if (!inspection) return <p><small>正在读取开放全文的方法、表格和图片...</small></p>
  return <div style={{ background: '#f8fafc', border: '1px solid #cbd5e1', padding: 10, margin: '8px 0' }}>
    <p style={{ marginTop: 0 }}><small>{inspection.extraction_status}</small></p>
    <strong>研究方法</strong>{inspection.methods.length ? inspection.methods.map((item) => <p key={`${item.section}-${item.text.slice(0, 20)}`}><small>{item.section}：</small>{item.text}</p>) : <p>原文未定位到方法章节。</p>}
    <strong>技术路线</strong>{inspection.technical_route.length ? <ol>{inspection.technical_route.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ol> : <p>原文未定位到可整理的技术路线。</p>}
    <strong>相关数据表</strong><p><small>{inspection.data_summary}</small></p>{inspection.data_tables.length ? inspection.data_tables.map((table, index) => <details key={`${table.caption}-${index}`}><summary>{table.caption}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>{table.data}</pre></details>) : <p>原文未定位到表格。</p>}
    <strong>相关图片</strong>{inspection.figures.length ? inspection.figures.map((figure, index) => <figure key={`${figure.caption}-${index}`} style={{ margin: '8px 0' }}>{figure.image_url ? <img src={figure.image_url} alt={figure.caption} style={{ maxWidth: '100%', maxHeight: 260 }} /> : <p><small>来源没有提供可嵌入的图片预览。</small></p>}<figcaption>{figure.caption} · <a href={figure.source_url} target="_blank" rel="noreferrer">查看原文</a></figcaption></figure>) : <p>原文未定位到图片。</p>}
    <small>{inspection.note}</small>
  </div>
}

function orderCatalog(papers: CatalogPaper[], sortOrder: string) {
  if (sortOrder === 'year_desc') return [...papers].sort((left, right) => Number(right.year ?? 0) - Number(left.year ?? 0))
  if (sortOrder === 'impact_factor_desc') return [...papers].sort((left, right) => (right.impact_factor ?? -1) - (left.impact_factor ?? -1))
  return papers
}

function pageWindow(currentPage: number, totalPages: number) {
  const first = Math.max(1, Math.min(currentPage - 2, totalPages - 4))
  const last = Math.min(totalPages, first + 4)
  return Array.from({ length: Math.max(0, last - first + 1) }, (_, index) => first + index)
}

const sectionStyle = { borderBottom: '1px solid #d9e0e8', padding: '20px 0' }
const buttonStyle = { background: '#0f766e', color: '#fff', border: 0, borderRadius: 4, padding: '9px 13px', marginTop: 10, cursor: 'pointer' }
const resultStyle = { background: '#eef6ff', padding: 14, marginTop: 12 }

createRoot(document.getElementById('root')!).render(<App />)

import { createContext, useContext, useState, type ReactNode } from 'react'

export type Lang = 'zh' | 'en'

const zh = {
  appName: '引文检查器',
  // upload
  eyebrow: 'AI 驱动 · APA 第七版',
  h1: '提交前，验证每一条引用。',
  body: '上传您的论文，在约 5 秒内收到彩色标注的引用核查报告——检测虚假引用、元数据错误和 APA 格式违规。',
  dropLabel: '拖拽上传论文',
  dropActive: '将文件拖放至此',
  dropHint: '点击选择文件',
  dropAccept: '.docx 或 .pdf 文件',
  pills: ['虚假引用检测', '元数据不一致', 'APA 格式违规', '引用验证通过'],
  privacy: '无需登录 · 报告在 24 小时后自动删除 · 文件不会被存储',
  invalidFile: '仅支持 .docx 和 .pdf 文件',
  // loading
  loadingTitle: '正在检查您的引用…',
  loadingEta: '约 5 秒',
  loadingSteps: [
    { label: '解析文档',          detail: '正在提取段落与参考文献部分…'           },
    { label: '提取引用',          detail: '正在识别正文引用与参考文献条目…'         },
    { label: '通过 Crossref 验证', detail: '正在核查参考文献…'                    },
    { label: '运行 APA 格式校验',  detail: '应用 APA 第七版格式规则…'             },
    { label: '生成报告',          detail: '交叉比对正文引用与参考文献列表…'         },
  ],
  loadingError: '上传失败，请稍后重试。',
  backToUpload: '返回重新上传',
  // report top bar
  citationsFound: (n: number) => `共发现 ${n} 条引用`,
  scoreLabels: { perfect: '全部通过', good: '基本良好', bad: '需要修改' },
  scoreTitle: '引用得分',
  countLabels: { pass: '通过', warning: '警告', error: '错误' },
  doiTip: '添加 DOI 可消除引用歧义',
  // issue panel
  filterAll: '全部',
  filterError: '错误',
  filterWarning: '警告',
  filterPass: '通过',
  noIssues: '🎉 没有问题！',
  expired: '已过期',
  expiresIn: (h: number, m: number) => `${h}h ${m}m 后过期`,
  copyLink: '复制分享链接',
  // issue card
  kindIntext: '正文引用',
  kindReference: '参考文献',
  verifiedMsg: '引用已验证 · 作者、年份、标题及期刊均匹配 · APA 格式正确',
  // essay panel
  essayHeading: '正文',
  referencesHeading: '参考文献',
  // status badge
  pass: 'Pass', warning: 'Warning', error: 'Error',
  // category tag
  catContent: '内容', catFormat: '格式', catOrphan: '孤立引用', catAmbiguous: '模糊引用',
  // error
  loadReportError: '加载报告失败',
}

const en: typeof zh = {
  appName: 'Citation Checker',
  eyebrow: 'AI-Powered · APA 7th Edition',
  h1: 'Verify every citation before you submit.',
  body: 'Upload your essay and receive a colour-annotated report in seconds — checking for fabricated references, metadata errors, and APA format violations.',
  dropLabel: 'Drag & drop your essay',
  dropActive: 'Drop your essay here',
  dropHint: 'click to browse',
  dropAccept: '.docx or .pdf files',
  pills: ['Fabricated references', 'Metadata mismatches', 'APA format violations', 'Verified citations'],
  privacy: 'No login required · Reports auto-delete after 24 hours · Files never stored',
  invalidFile: 'Only .docx and .pdf files are supported',
  loadingTitle: 'Checking your citations…',
  loadingEta: '~5 seconds',
  loadingSteps: [
    { label: 'Parsing document',       detail: 'Extracting paragraphs and reference section…'              },
    { label: 'Extracting citations',   detail: 'Finding in-text citations and reference entries…'           },
    { label: 'Verifying with Crossref', detail: 'Checking references against academic databases…'           },
    { label: 'Running APA validator',  detail: 'Applying APA 7th format rules…'                            },
    { label: 'Building report',        detail: 'Cross-referencing in-text citations with reference list…'  },
  ],
  loadingError: 'Upload failed. Please try again.',
  backToUpload: 'Back to upload',
  citationsFound: (n: number) => `${n} citations found`,
  scoreLabels: { perfect: 'All Clear', good: 'Mostly Good', bad: 'Needs Review' },
  scoreTitle: 'citation score',
  countLabels: { pass: 'pass', warning: 'warning', error: 'error' },
  doiTip: 'Adding DOIs removes ambiguity',
  filterAll: 'All',
  filterError: 'Error',
  filterWarning: 'Warning',
  filterPass: 'Pass',
  noIssues: '🎉 No issues!',
  expired: 'Expired',
  expiresIn: (h: number, m: number) => `Expires in ${h}h ${m}m`,
  copyLink: 'Copy share link',
  kindIntext: 'in-text',
  kindReference: 'reference',
  verifiedMsg: 'Reference verified · Author, year, title, and journal match · APA format correct',
  essayHeading: 'Essay Text',
  referencesHeading: 'References',
  pass: 'Pass', warning: 'Warning', error: 'Error',
  catContent: 'Content', catFormat: 'Format', catOrphan: 'Orphan', catAmbiguous: 'Ambiguous',
  loadReportError: 'Failed to load report',
}

export type Translations = typeof zh

interface LangCtx {
  lang: Lang
  t: Translations
  toggle: () => void
}

const Ctx = createContext<LangCtx>({ lang: 'zh', t: zh, toggle: () => {} })

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>('zh')
  const toggle = () => setLang((l) => (l === 'zh' ? 'en' : 'zh'))
  return <Ctx.Provider value={{ lang, t: lang === 'zh' ? zh : en, toggle }}>{children}</Ctx.Provider>
}

export function useT() {
  return useContext(Ctx)
}

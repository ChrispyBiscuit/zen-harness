export type Display = { type: string; [k: string]: any }

export type Approval = { call_id: string; rule_label?: string; warnings?: string[] }

export type Tool = {
  name: string
  kind: string
  desc?: string
  by?: string
  result?: string | null
  ok?: boolean
  awaiting?: boolean
  approval?: Approval
  display?: Display | null
  reverted?: boolean
  has_image?: boolean
}

export type Attachment = { name: string; path: string; kind: string; size: number }

export type TurnStats = {
  steps: number
  tool_calls: number
  prompt_tokens?: number
  completion_tokens?: number
  seconds: number
  context_used?: number
  context_max?: number
  compactions?: number
  failed_calls?: number
  advisor_cost?: number
}

export type Msg = {
  role: "user" | "assistant"
  content: string
  thinking?: string
  streaming?: boolean
  kind?: string
  id?: string
  tool?: Tool
  stats?: TurnStats
  images?: { src: string; page: string; title?: string; cited?: boolean; name?: string; publisher?: string; listing?: boolean }[]
  attachments?: Attachment[]
  interjection?: boolean
  error?: boolean
  files?: Record<string, string>
  reverts?: Display[]
  undone?: boolean
  summary?: string
  before?: number
  after?: number
  task_id?: string
  title?: string
  status?: string
  conv_id?: string
  result?: string
  t?: number
  budget?: number
  tokens?: number
  seconds?: number
  _v?: number
}

export type WorkerStep = { name: string; args: string; secs: number | null; preview: string; ok?: boolean }
export type Worker = {
  i: number; title: string; brief: string; status: "queued" | "running" | "done" | "error" | "stopped" | "taken over"
  action?: string; steps?: WorkerStep[]; tokens?: number; tps?: number; started?: number; ended?: number | null
  report?: string; error?: string
}
export type Workers = { model: string; items: Worker[]; ts?: number }

export type Conv = {
  id: string
  title: string
  messages: Msg[]
  workers?: Workers
  running?: boolean
  status?: string
  mode?: string
  parent?: string
  task_id?: string
}

export type Thread = {
  id: string
  title: string
  updated: number
  running: boolean
  waiting: boolean
  parent?: string | null
  task_id?: string | null
  mode?: string | null
  snippet: string
  run_started?: number | null
  run_ended?: number | null
}

export type Task = {
  type: "thread" | "run" | "bg"
  id: string
  title: string
  status: string
  started: number
  ended: number | null
  conv_id?: string | null
  command?: string
  cwd?: string
  pid?: number
  log?: string
  result?: string
  tracker?: Tracker | null
  parent?: string
}

export type TrackerStep = { step: string; status: string }
export type Tracker = { goal?: string; steps?: TrackerStep[]; notes?: string[]; next?: string }

export type Settings = {
  temperature: number
  num_ctx: number
  max_steps: number
  system_prompt: string
  agent: Record<string, any>
  ui: { mode: "agent" | "chat" | "speech"; think: boolean; chat_model: string; notifications?: boolean; speech_model?: string }
  [k: string]: any
}

import type { components } from './schema'
export type Settings = Required<components['schemas']['ViewSettings']>
export type Config = Required<components['schemas']['ProjectConfig']>
export type View = Omit<components['schemas']['ViewInfo'], 'settings'> & {settings: Settings}
export type Project = Omit<components['schemas']['ProjectInfo'], 'config'|'views'> & {config: Config; views: View[]}
export type Job = components['schemas']['JobInfo']
export type ViewName = View['name']
export type Stats = { vertices: number; faces: number; watertight: boolean; winding_consistent: boolean; components: number; dimensions: Record<'length'|'width'|'height', number>; unit: string; warnings: string[]; source_views: string[]; projection_iou: Record<string,number>; resolution: number }
export type Result = { stats: Stats; artifacts: Record<string,string>; projections: Record<string,string>; elapsed_seconds: number; cached: boolean; revision: number }
export const artifact = (id: string, download = false) => `/api/artifacts/${id}${download ? '?download=true' : ''}`
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'The local server did not respond. Check the launcher and retry.' }))
    throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.map((x: {msg:string}) => x.msg).join('; ') || 'Request failed')
  }
  return response.json()
}
export const send = (body: unknown): RequestInit => ({ body: JSON.stringify(body) })

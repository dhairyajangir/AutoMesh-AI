import { useEffect, useRef, useState } from 'react'
import { Brush, Eraser, Undo2, Redo2, Pentagon, ZoomIn, ZoomOut, Check, Eye, RotateCcw } from 'lucide-react'

type Point = { x: number; y: number }
type Props = { imageUrl: string; maskUrl?: string; disabled?: boolean; onSave: (blob: Blob) => Promise<void>; onDirty: (dirty: boolean) => void }

export default function MaskEditor({ imageUrl, maskUrl, disabled=false, onSave, onDirty }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 800, height: 400 })
  const [tool, setTool] = useState<'brush'|'erase'|'polygon'>('brush')
  const [radius, setRadius] = useState(18)
  const [zoom, setZoom] = useState(1)
  const [opacity, setOpacity] = useState(0.55)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [points, setPoints] = useState<Point[]>([])
  const [historyIndex, setHistoryIndex] = useState(0)
  const [editVersion, setEditVersion] = useState(0)
  const ownSave = useRef(false)
  const savingRef = useRef(false)
  const saveAction = useRef<() => Promise<void>>(async () => {})
  const history = useRef<string[]>([])
  const indexRef = useRef(0)
  const drawing = useRef(false)
  const lastPoint = useRef<Point|null>(null)
  const dirtyCallback = useRef(onDirty)
  dirtyCallback.current = onDirty

  const markDirty = (value: boolean) => { setDirty(value); dirtyCallback.current(value) }
  const checkpoint = () => {
    if (!canvas.current) return
    history.current = history.current.slice(0, indexRef.current + 1)
    history.current.push(canvas.current.toDataURL())
    if (history.current.length > 20) history.current.shift()
    indexRef.current = history.current.length - 1
    setHistoryIndex(indexRef.current)
    setEditVersion(v => v + 1)
    setError('')
    markDirty(true)
  }
  useEffect(() => {
    // The server returned our own saved pixels. Keep the current undo history.
    if (ownSave.current) { ownSave.current = false; return }
    let disposed = false
    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const source = new Image()
        source.src = imageUrl
        await source.decode()
        if (disposed) return
        setSize({ width: source.naturalWidth, height: source.naturalHeight })
        const element = canvas.current!
        element.width = source.naturalWidth
        element.height = source.naturalHeight
        const ctx = element.getContext('2d')!
        ctx.clearRect(0, 0, element.width, element.height)
        if (maskUrl) {
          const mask = new Image()
          mask.src = maskUrl
          await mask.decode()
          if (disposed) return
          ctx.drawImage(mask, 0, 0, element.width, element.height)
          const image = ctx.getImageData(0, 0, element.width, element.height)
          for (let i = 0; i < image.data.length; i += 4) {
            const alpha = image.data[i] > 127 ? 255 : 0
            image.data[i] = 71; image.data[i+1] = 217; image.data[i+2] = 204; image.data[i+3] = alpha
          }
          ctx.putImageData(image, 0, 0)
        }
        history.current = [element.toDataURL()]
        indexRef.current = 0
        setHistoryIndex(0)
        markDirty(false)
        setPoints([])
      } catch { if (!disposed) setError('The image could not load. Re-select the view or check the local server.') }
      finally { if (!disposed) setLoading(false) }
    }
    load()
    return () => { disposed = true; dirtyCallback.current(false) }
  }, [imageUrl, maskUrl])

  const coords = (e: React.PointerEvent): Point => {
    const box = canvas.current!.getBoundingClientRect()
    return { x: (e.clientX - box.left) / box.width * size.width, y: (e.clientY - box.top) / box.height * size.height }
  }
  const stroke = (p: Point) => {
    const ctx = canvas.current!.getContext('2d')!
    ctx.globalCompositeOperation = tool === 'erase' ? 'destination-out' : 'source-over'
    ctx.strokeStyle = '#47d9cc'; ctx.fillStyle = '#47d9cc'
    ctx.lineWidth = radius * 2; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
    ctx.beginPath()
    if (lastPoint.current) { ctx.moveTo(lastPoint.current.x, lastPoint.current.y); ctx.lineTo(p.x,p.y); ctx.stroke() }
    else { ctx.arc(p.x,p.y,radius,0,Math.PI*2); ctx.fill() }
    ctx.globalCompositeOperation = 'source-over'
    lastPoint.current = p
  }
  const restore = (idx: number) => {
    if (loading || disabled || savingRef.current) return
    const image = new Image()
    image.onload = () => {
      const c = canvas.current
      if (!c) return
      const ctx = c.getContext('2d')!
      ctx.clearRect(0,0,c.width,c.height); ctx.drawImage(image,0,0)
      indexRef.current = idx; setHistoryIndex(idx); setEditVersion(v => v + 1); setError(''); markDirty(true)
    }
    image.src = history.current[idx]
  }
  const finishPolygon = () => {
    if (points.length < 3 || loading || savingRef.current || disabled) return
    const ctx = canvas.current!.getContext('2d')!
    ctx.fillStyle = '#47d9cc'; ctx.beginPath(); ctx.moveTo(points[0].x,points[0].y)
    points.slice(1).forEach(p => ctx.lineTo(p.x,p.y)); ctx.closePath(); ctx.fill()
    setPoints([]); checkpoint()
  }
  const save = async () => {
    if (savingRef.current || disabled || !canvas.current) return
    savingRef.current = true
    const c = canvas.current!
    const output = document.createElement('canvas')
    output.width = c.width; output.height = c.height
    const data = c.getContext('2d')!.getImageData(0,0,c.width,c.height)
    for (let i=0;i<data.data.length;i+=4) {
      const value = data.data[i+3] > 127 ? 255 : 0
      data.data[i]=value; data.data[i+1]=value; data.data[i+2]=value; data.data[i+3]=255
    }
    output.getContext('2d')!.putImageData(data,0,0)
    setSaving(true); setError('')
    try {
      const blob = await new Promise<Blob>((resolve,reject) => output.toBlob(b => b ? resolve(b) : reject(new Error('Could not encode mask')), 'image/png'))
      ownSave.current = true
      await onSave(blob); markDirty(false)
    } catch (e) { ownSave.current = false; setError((e as Error).message) }
    finally { savingRef.current = false; setSaving(false) }
  }
  saveAction.current = save
  useEffect(() => {
    if (!dirty || saving || loading || error || disabled) return
    const timer = setTimeout(() => { if (!drawing.current) void saveAction.current() }, 2000)
    return () => clearTimeout(timer)
  }, [dirty, editVersion, saving, loading, error, disabled])
  return <div className="mask-editor">
    <div className="editor-toolbar">
      <div className="segmented">{([['brush',Brush,'Paint'],['erase',Eraser,'Erase'],['polygon',Pentagon,'Polygon']] as const).map(([key,Icon,label]) => <button key={key} className={tool===key?'selected':''} onClick={() => {setTool(key);setPoints([])}}><Icon size={15}/>{label}</button>)}</div>
      <label className="brush-label">Size <input aria-label="Brush size" type="range" min="2" max="100" value={radius} onChange={e => setRadius(+e.target.value)}/></label>
      <button className="icon-button" aria-label="Undo mask edit" disabled={historyIndex===0||saving} onClick={() => restore(historyIndex-1)}><Undo2 size={16}/></button>
      <button className="icon-button" aria-label="Redo mask edit" disabled={historyIndex>=history.current.length-1||saving} onClick={() => restore(historyIndex+1)}><Redo2 size={16}/></button>
      <button className="primary small save-mask" onClick={save} disabled={!dirty||saving||loading}><Check size={15}/>{saving?'Saving…':'Save mask'}</button>
    </div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    <div className="image-scroll">
      <div className="image-stage" style={{width:`${zoom*90}%`,aspectRatio:`${size.width}/${size.height}`}}>
        <img src={imageUrl} alt="Vehicle source beneath editable silhouette" draggable={false}/>
        <canvas ref={canvas} style={{opacity}} aria-label="Mask drawing canvas. Use the polygon point controls below as a keyboard alternative."
          onPointerDown={e => {if(loading||savingRef.current||disabled)return; const p=coords(e); if(tool==='polygon'){setPoints([...points,p]);return} drawing.current=true;lastPoint.current=null;e.currentTarget.setPointerCapture(e.pointerId);stroke(p)}}
          onPointerMove={e => {if(drawing.current)stroke(coords(e))}}
          onPointerUp={() => {if(drawing.current){drawing.current=false;lastPoint.current=null;checkpoint()}}}
          onPointerCancel={() => {if(drawing.current){drawing.current=false;checkpoint()}}}/>
        {points.length>0 && <svg className="polygon-preview" viewBox={`0 0 ${size.width} ${size.height}`}><polyline points={points.map(p=>`${p.x},${p.y}`).join(' ')} fill="#47d9cc22" stroke="#e8ffff" strokeWidth="3"/>{points.map((p,i)=><circle key={i} cx={p.x} cy={p.y} r="5" fill="#47d9cc"/>)}</svg>}
      </div>
      {loading && <div className="loading-overlay">Loading image…</div>}
    </div>
    {tool==='polygon' && <div className="polygon-controls"><span>{points.length} points</span><label>X <input id="point-x" type="number" defaultValue="50" min="0" max="100"/>%</label><label>Y <input id="point-y" type="number" defaultValue="50" min="0" max="100"/>%</label><button disabled={loading||saving||disabled} onClick={() => {const x=+(document.getElementById('point-x') as HTMLInputElement).value;const y=+(document.getElementById('point-y') as HTMLInputElement).value;setPoints([...points,{x:Math.max(0,Math.min(100,x))/100*size.width,y:Math.max(0,Math.min(100,y))/100*size.height}])}}>Add point</button><button disabled={points.length<3||loading||saving||disabled} onClick={finishPolygon}>Fill polygon</button><button onClick={()=>setPoints([])}>Clear points</button></div>}
    <div className="editor-status"><span>{dirty?'Unsaved mask changes':'Mask editor'} <span className="muted">· {size.width} × {size.height}</span></span><div className="control-group"><button className="icon-button" aria-label="Toggle mask overlay" onClick={()=>setOpacity(opacity===0?0.55:0)}><Eye size={15}/></button><button className="icon-button" aria-label="Zoom out" onClick={()=>setZoom(Math.max(0.5,zoom-0.25))}><ZoomOut size={15}/></button><span>{Math.round(zoom*100)}%</span><button className="icon-button" aria-label="Zoom in" onClick={()=>setZoom(Math.min(4,zoom+0.25))}><ZoomIn size={15}/></button><button className="icon-button" aria-label="Reset image zoom" onClick={()=>setZoom(1)}><RotateCcw size={15}/></button></div></div>
  </div>
}

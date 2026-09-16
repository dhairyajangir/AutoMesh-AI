import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { Center, Grid, OrbitControls, useGLTF, GizmoHelper, GizmoViewport } from '@react-three/drei'
import * as THREE from 'three'
import { Grid3X3, Maximize, RotateCcw, ScanLine, Box } from 'lucide-react'

function Model({ url, wire }: { url: string; wire: boolean }) {
  const { gl } = useThree()
  const gltf = useGLTF(url)
  const { model, scale } = useMemo(() => {
    const model = gltf.scene.clone(true)
    const box = new THREE.Box3().setFromObject(model)
    const size = box.getSize(new THREE.Vector3())
    model.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        if (!obj.geometry.attributes.normal) obj.geometry.computeVertexNormals()
        obj.material = new THREE.MeshStandardMaterial({ color: '#9bbcc3', roughness: 0.42, metalness: 0.3, wireframe: wire, side: THREE.FrontSide })
      }
    })
    return { model, scale: 5.8 / Math.max(size.x, size.y, size.z) }
  }, [gltf, wire])
  useEffect(() => () => model.traverse(obj => { if (obj instanceof THREE.Mesh) (obj.material as THREE.Material).dispose() }), [model])
  useEffect(() => { gl.domElement.dataset.modelReady = url; return () => { delete gl.domElement.dataset.modelReady } }, [gl, url])
  return <Center top><primitive object={model} scale={scale} /></Center>
}

function CameraControls({ cameraView }: {cameraView: string}) {
  const controls = useRef<any>(null)
  const { camera, invalidate, gl } = useThree()
  useEffect(() => {
    const positions: Record<string, number[]> = { perspective: [6, 3.5, 6], front: [9, 1, 0], side: [0, 1, 9], top: [0, 10, 0.001], rear: [-9, 1, 0] }
    const p = positions[cameraView.split(':')[0]] || positions.perspective
    camera.position.set(p[0], p[1], p[2])
    controls.current?.target.set(0, 0.8, 0)
    controls.current?.update()
    invalidate()
  }, [cameraView, camera, invalidate])
  useEffect(() => {
    const canvas = gl.domElement
    canvas.tabIndex = 0
    canvas.setAttribute('aria-label', '3D model. Arrow keys orbit, plus and minus zoom. Use view buttons for standard orientations.')
    const key = (e: KeyboardEvent) => {
      if (!controls.current) return
      const delta = camera.position.clone().sub(controls.current.target)
      if (e.key === '+' || e.key === '=') delta.multiplyScalar(0.9)
      else if (e.key === '-') delta.multiplyScalar(1.1)
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') delta.applyAxisAngle(new THREE.Vector3(0, 1, 0), e.key === 'ArrowLeft' ? -0.12 : 0.12)
      else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') delta.y += e.key === 'ArrowUp' ? 0.3 : -0.3
      else return
      e.preventDefault()
      camera.position.copy(controls.current.target).add(delta)
      controls.current.update()
      invalidate()
    }
    canvas.addEventListener('keydown', key)
    return () => canvas.removeEventListener('keydown', key)
  }, [camera, gl, invalidate])
  return <OrbitControls ref={controls} makeDefault enableDamping={false} minDistance={3} maxDistance={20} maxPolarAngle={Math.PI * 0.9} />
}

export default function Viewer({ url }: { url: string }) {
  const [wire, setWire] = useState(false)
  const [grid, setGrid] = useState(true)
  const [cameraView, setCameraView] = useState('perspective')
  const host = useRef<HTMLDivElement>(null)
  return <div className="model-view" ref={host} data-testid="model-viewer">
    <div className="viewport-controls">
      <div className="segmented"><button className={!wire ? 'selected' : ''} onClick={() => setWire(false)}><Box size={14}/>Solid</button><button className={wire ? 'selected' : ''} onClick={() => setWire(true)}><ScanLine size={14}/>Wireframe</button></div>
      <div className="control-group"><button className={`icon-button ${grid ? 'active' : ''}`} title="Toggle grid" aria-label="Toggle grid" onClick={() => setGrid(!grid)}><Grid3X3 size={17}/></button><button className="icon-button" title="Reset camera" aria-label="Reset camera" onClick={() => setCameraView(`perspective:${Date.now()}`)}><RotateCcw size={17}/></button><button className="icon-button" title="Fullscreen viewer" aria-label="Fullscreen viewer" onClick={() => { if (document.fullscreenElement) document.exitFullscreen(); else host.current?.requestFullscreen() }}><Maximize size={17}/></button></div>
    </div>
    <Canvas frameloop="demand" dpr={[1, 1.5]} camera={{position:[7,4,7], fov:40, near:0.01, far:100}} gl={{antialias:true, powerPreference:'low-power'}} fallback={<div className="empty">WebGL is unavailable. Your mesh files can still be downloaded.</div>}>
      <color attach="background" args={['#171e24']}/>
      <ambientLight intensity={1.4}/>
      <directionalLight position={[4,8,5]} intensity={3.1} color="#e2faff"/>
      <directionalLight position={[-4,3,-4]} intensity={2} color="#607e9c"/>
      <Suspense fallback={null}><Model url={url} wire={wire}/></Suspense>
      {grid && <Grid position={[0,-0.015,0]} args={[18,18]} cellSize={0.5} cellThickness={0.5} cellColor="#34414a" sectionSize={2} sectionThickness={0.8} sectionColor="#50616d" fadeDistance={18} fadeStrength={1.5} infiniteGrid/>}
      <CameraControls cameraView={cameraView}/>
      <GizmoHelper alignment="bottom-right" margin={[58,65]}><GizmoViewport axisColors={['#ef8e88','#83bba0','#86b8e1']} labelColor="#152028" hideNegativeAxes/></GizmoHelper>
    </Canvas>
    <div className="camera-presets">{['perspective','front','side','top'].map(v => <button key={v} className={cameraView.startsWith(v) ? 'selected' : ''} onClick={() => setCameraView(`${v}:${Date.now()}`)}>{v === 'perspective' ? '3D' : v}</button>)}</div>
    <div className="viewport-hint">Drag to orbit <span>·</span> Scroll to zoom <span>·</span> Shift + drag to pan</div>
  </div>
}

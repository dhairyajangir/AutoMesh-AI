import {test,expect} from '@playwright/test'

test('measure visible viewer orbit cadence',async({page,request},testInfo)=>{
  const projects=await (await request.get('/api/projects')).json()
  const example=projects.find((p:any)=>p.name==='Touring coupe · example')
  await page.goto('/?project='+example.id)
  const canvas=page.locator('canvas')
  await expect(canvas).toBeVisible({timeout:45000})
  await expect(canvas).toHaveAttribute('data-model-ready', /artifacts/, {timeout:45000})
  await page.getByRole('button',{name:'side',exact:true}).click()
  const result=await page.evaluate(async()=>{
    const canvas=document.querySelector('canvas')!
    const gl=canvas.getContext('webgl2')
    const ext=gl?.getExtension('WEBGL_debug_renderer_info')
    const renderer=ext?gl!.getParameter(ext.UNMASKED_RENDERER_WEBGL):'not exposed'
    const times:number[]=[]
    const start=performance.now()
    for(let i=0;i<120;i++){
      await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()))
      canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}))
      times.push(performance.now())
    }
    return {renderer,frames:times.length,elapsed_ms:times.at(-1)!-start,orbit_updates_per_second:120000/(times.at(-1)!-start),viewport:{width:innerWidth,height:innerHeight},pixel_ratio:devicePixelRatio}
  })
  await testInfo.attach('viewer-performance',{body:JSON.stringify(result,null,2),contentType:'application/json'})
  console.log('VIEWER_BENCHMARK',JSON.stringify(result))
  expect(result.frames).toBe(120)
  await page.getByRole('button',{name:'3D',exact:true}).click()
  await page.screenshot({path:'../validation/studio-desktop.png',fullPage:true})
})

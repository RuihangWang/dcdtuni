import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync, writeFileSync, realpathSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
const root=realpathSync(__dirname)
const evidence=process.env.LRM_EVIDENCE_DIR!
const captured:Record<string,string>={}
export default defineConfig({root,cacheDir:resolve(evidence,'vite-cache'),plugins:[{
 name:'owned-monitor-source-proof',transform(_code,id){
  const file=id.split('?')[0]!
  if(file.endsWith('/components/LiquidRuntimeMonitor.tsx')||file.endsWith('/liquid/liquidRuntimeClient.ts')||file.endsWith('/components/LiquidExperimentPanel.tsx')) {
   const actual=realpathSync(file)
   if(!actual.startsWith(root+'/src/'))throw new Error('Monitor test imported outside owned candidate: '+actual)
   captured[actual]=createHash('sha256').update(readFileSync(actual)).digest('hex')
  }
 },closeBundle(){writeFileSync(resolve(evidence,'actual-import-source.json'),JSON.stringify(captured,null,2))}
},react()],test:{environment:'jsdom',maxWorkers:1,minWorkers:1}})

import {defineConfig} from 'vitest/config'
const T=__dirname
export default defineConfig({root:T,cacheDir:T+'/cache/vite',
 plugins:[{name:'private-variant',enforce:'pre',transform(code,id){
 if(id.endsWith('.test.ts'))return {code:code.replaceAll('/VARIANT/','/'+(process.env.MMS_VARIANT||'after')+'/'),map:null}
 }}],test:{environment:'jsdom',include:['fine.test.ts'],maxWorkers:1,minWorkers:1,fileParallelism:false,pool:'forks'}})

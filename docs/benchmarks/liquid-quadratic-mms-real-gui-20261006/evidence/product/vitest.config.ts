import {defineConfig} from 'vitest/config'
export default defineConfig({root:__dirname,cacheDir:__dirname+'/cache/vite',test:{environment:'jsdom',include:['compile.test.ts'],maxWorkers:1,minWorkers:1,fileParallelism:false,pool:'forks'}})

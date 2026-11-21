import {defineConfig} from 'vitest/config';
import path from 'node:path';
export default defineConfig({
  root: path.dirname(new URL(import.meta.url).pathname),
  resolve:{alias:{'@private-snapshot':path.join(process.env.OV_PRIVATE_TREE,'src/frontend/react/src/renderer/sceneSnapshot.ts')}},
  test:{environment:'node',include:['tests/*.test.ts'],minWorkers:1,maxWorkers:1,fileParallelism:false}
});

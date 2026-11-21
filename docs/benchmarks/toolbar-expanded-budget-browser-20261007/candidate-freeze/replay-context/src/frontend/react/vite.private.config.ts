import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
export default defineConfig({plugins:[react()],test:{environment:'jsdom'},define:{__DCDTUNI_OV_RETURN_DIAGNOSTICS__:'false'}})

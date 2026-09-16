import {defineConfig} from '@playwright/test'

export default defineConfig({
  testDir: './e2e', timeout: 90000, workers: 1, retries: 0,
  use: {baseURL: 'http://127.0.0.1:8765', channel: 'msedge', headless: true, viewport: {width:1440,height:1000}, screenshot:'only-on-failure', trace:'retain-on-failure'},
  reporter: [['list'],['json',{outputFile:'test-results/results.json'}]],
})

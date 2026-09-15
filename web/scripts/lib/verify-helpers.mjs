import { sleep } from './browser-harness.mjs'

export async function waitFor(check, timeout = 5000) {
  const until = Date.now() + timeout
  let last = null
  while (Date.now() < until) {
    last = await check()
    if (last) return last
    await sleep(50)
  }
  return last
}

export async function record(report, name, run) {
  try {
    report.cases.push({ name, ...(await run()) })
  } catch (error) {
    report.cases.push({ name, ok: false, got: String(error?.message ?? error) })
  }
}

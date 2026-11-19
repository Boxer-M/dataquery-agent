import assert from 'node:assert/strict'
import { test } from 'node:test'
import { artifactReceipt, exportControl, exportSession } from '../src/xlsx-export.ts'

test('server eligibility disables grouped and legacy records without inferring row count', () => {
  const grouped = '当前 Excel 导出仅支持汇总结果，暂不支持分组明细'
  const legacy = '此查询缺少导出快照，请重新运行查询后再导出'
  for (const [name, eligible, message] of [['summary', true, '支持导出'],
    ['month', false, grouped], ['channel', false, grouped],
    ['one_group_row', false, grouped], ['legacy', false, legacy]]) {
    assert.deepEqual(exportControl({ eligible, message }),
      { disabled: !eligible, message: eligible ? '' : message }, name)
  }
  assert.equal(exportControl().disabled, true)
})

const query = '2f074dc9-2d98-4824-a765-09fc65ead286'
const id = '3f074dc9-2d98-4824-a765-09fc65ead286'
const receipt = { artifact_id: id, query_id: query, filename: `metrics-${id}.xlsx`,
  sha256: 'a'.repeat(64), byte_size: 50, created_at: '2026-11-19T00:00:00Z' }

test('rapid repeated clicks share one pending create and download', async () => {
  let release
  let creates = 0, downloads = 0
  const files = [], stages = []
  const session = exportSession(query, {
    create: async () => { creates++; await new Promise(resolve => { release = resolve }); return receipt },
    download: async () => { downloads++; return new Blob(['workbook']) },
    save: (_, filename) => files.push(filename),
  })
  const first = session.run(value => stages.push(value))
  assert.equal(session.run(() => {}), first)
  release()
  await first
  assert.equal(creates, 1)
  assert.equal(downloads, 1)
  assert.deepEqual(files, [receipt.filename])
  assert.deepEqual(stages, ['creating', 'downloading', 'completed'])
})

test('download failure retains server artifact and retries GET without POST', async () => {
  let creates = 0, downloads = 0, saves = 0
  const session = exportSession(query, {
    create: async () => { creates++; return receipt },
    download: async artifact => {
      assert.equal(artifact.artifact_id, id)
      if (++downloads === 1) throw new Error('network failure')
      return new Blob(['workbook'])
    },
    save: () => { saves++ },
  })
  await assert.rejects(session.run(() => {}), /network failure/)
  assert.equal(session.hasArtifact(), true)
  await session.run(() => {})
  assert.deepEqual([creates, downloads, saves], [1, 2, 1])
})

test('creation rejection never downloads or marks a receipt as available', async () => {
  const session = exportSession(query, {
    create: async () => { throw new Error('409 snapshot unavailable') },
    download: async () => { assert.fail('unexpected download') },
    save: () => { assert.fail('unexpected save') },
  })
  await assert.rejects(session.run(() => {}), /409/)
  assert.equal(session.hasArtifact(), false)
  for (const changed of [{ query_id: id }, { filename: '../report.xlsx' }, { sha256: 'invalid' },
    { byte_size: 1024 * 1024 + 1 }, { artifact_id: 'unknown' }]) {
    assert.throws(() => artifactReceipt({ ...receipt, ...changed }, query), /不完整或与当前查询不匹配/)
  }
})

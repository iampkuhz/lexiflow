import test from 'node:test';
import assert from 'node:assert/strict';
import { formatIncrementalStateDiagnostic } from './incremental-acceptance.mjs';

test('incremental timeout diagnostic reports synthetic state without caption text', () => {
 const summary=formatIncrementalStateDiagnostic({
  overlayState:'loading',sourceCaptionLength:10,
  diagnostics:{counts:{observed:1,requested:1,ready:0}},requestCount:1
 });
 assert.deepEqual(JSON.parse(summary),{
  overlayState:'loading',sourceCaptionLength:10,
  counts:{observed:1,requested:1,ready:0},requestCount:1
 });
 assert.equal(summary.includes('A reliable'),false);
});

test('incremental timeout diagnostic tolerates absent overlay and counters', () => {
 assert.deepEqual(JSON.parse(formatIncrementalStateDiagnostic({
  overlayState:null,sourceCaptionLength:0,diagnostics:null,requestCount:0
 })),{overlayState:null,sourceCaptionLength:0,counts:null,requestCount:0});
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  hasTopLevelKey,
  isImmutableExternalReference,
  isReusableWorkflow,
  usesReferences,
  validateWorkflow,
} = require('./index.js');

test('accepts local, self-repository, docker, and full-SHA refs', () => {
  assert.equal(isImmutableExternalReference('./.github/actions/local'), true);
  assert.equal(isImmutableExternalReference('$/.github/workflows/version-intent.yml'), true);
  assert.equal(isImmutableExternalReference('docker://alpine:3.20'), true);
  assert.equal(
    isImmutableExternalReference('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1'),
    true,
  );
});

test('rejects mutable external refs', () => {
  assert.equal(isImmutableExternalReference('actions/checkout@v4'), false);
  assert.equal(isImmutableExternalReference('owner/repo/action@main'), false);
  assert.equal(isImmutableExternalReference('owner/repo/action'), false);
});

test('extracts step and reusable-workflow uses refs', () => {
  const text = `jobs:\n  call:\n    uses: owner/repo/.github/workflows/reuse.yml@0123456789012345678901234567890123456789\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # pinned\n`;
  assert.deepEqual(usesReferences(text), [
    'owner/repo/.github/workflows/reuse.yml@0123456789012345678901234567890123456789',
    'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
  ]);
});

test('requires top-level permissions and optional concurrency', () => {
  const valid = `name: CI\non:\n  push:\npermissions:\n  contents: read\nconcurrency:\n  group: ci\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n`;
  assert.deepEqual(validateWorkflow('ci.yml', valid, { requireConcurrency: true }), []);

  const errors = validateWorkflow('ci.yml', 'name: CI\njobs: {}\n', { requireConcurrency: true });
  assert.equal(errors.some((value) => value.includes('permissions')), true);
  assert.equal(errors.some((value) => value.includes('concurrency')), true);
});

test('reusable workflows are exempt from caller concurrency requirement', () => {
  const text = `name: Reusable\non:\n  workflow_call:\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n`;
  assert.equal(isReusableWorkflow(text), true);
  assert.deepEqual(validateWorkflow('reuse.yml', text, { requireConcurrency: true }), []);
});

test('write-all is rejected and top-level key detection is indentation-aware', () => {
  assert.equal(hasTopLevelKey('permissions:\n  contents: read\n', 'permissions'), true);
  assert.equal(hasTopLevelKey('jobs:\n  x:\n    permissions:\n      contents: read\n', 'permissions'), false);
  const errors = validateWorkflow('ci.yml', 'permissions: write-all\njobs: {}\n');
  assert.equal(errors.some((value) => value.includes('write-all')), true);
});

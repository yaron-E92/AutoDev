'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const test = require('node:test');

const policy = require('./index.js');

function git(cwd, ...args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

function setupRepo() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autodev-version-'));
  const remote = path.join(root, 'remote.git');
  const repo = path.join(root, 'repo');
  execFileSync('git', ['init', '--bare', remote], { stdio: 'ignore' });
  execFileSync('git', ['init', '-b', 'main', repo], { stdio: 'ignore' });
  git(repo, 'config', 'user.name', 'AutoDev Tests');
  git(repo, 'config', 'user.email', 'autodev-tests@example.invalid');
  git(repo, 'remote', 'add', 'origin', remote);
  return { root, remote, repo };
}

function commit(repo, message) {
  const marker = path.join(repo, 'marker.txt');
  fs.appendFileSync(marker, `${message}\n`, 'utf8');
  git(repo, 'add', 'marker.txt');
  git(repo, 'commit', '-m', message);
  return git(repo, 'rev-parse', 'HEAD');
}

test('exact intent accepts one directive and rejects missing/duplicates/conflicts', () => {
  assert.equal(policy.parseExactIntent('text\n+semver: minor\n'), 'minor');
  assert.throws(() => policy.parseExactIntent('no directive'), /exactly one version intent/);
  assert.throws(() => policy.parseExactIntent('+semver: patch\n+semver: patch\n'), /duplicate or conflicting/);
  assert.throws(() => policy.parseExactIntent('+semver: patch\n+semver: major\n'), /duplicate or conflicting/);
});

test('highest bump and semantic resets are deterministic', () => {
  assert.equal(policy.highestBump([]), 'none');
  assert.equal(policy.highestBump(['none', 'patch']), 'patch');
  assert.equal(policy.highestBump(['patch', 'minor', 'none']), 'minor');
  assert.equal(policy.highestBump(['minor', 'major', 'patch']), 'major');
  assert.deepEqual(policy.bumpVersion({ major: 1, minor: 2, patch: 3 }, 'patch'), { major: 1, minor: 2, patch: 4 });
  assert.deepEqual(policy.bumpVersion({ major: 1, minor: 2, patch: 3 }, 'minor'), { major: 1, minor: 3, patch: 0 });
  assert.deepEqual(policy.bumpVersion({ major: 1, minor: 2, patch: 3 }, 'major'), { major: 2, minor: 0, patch: 0 });
});

test('PR candidate uses latest reachable canonical tag without Python or project setup', () => {
  const { root, repo } = setupRepo();
  try {
    commit(repo, 'base');
    git(repo, 'tag', '-a', 'v1.2.3', '-m', 'base');
    commit(repo, 'change');
    const result = policy.candidateForPr({ body: '+semver: minor', cwd: repo });
    assert.equal(result.base_tag, 'v1.2.3');
    assert.equal(result.bump, 'minor');
    assert.equal(result.version, '1.3.0');
    assert.equal(result.tag, 'v1.3.0');
    assert.equal(result.tag_required, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('resolve-main selects highest associated PR intent and none never tags', async () => {
  const { root, repo } = setupRepo();
  const originalFetch = global.fetch;
  try {
    const base = commit(repo, 'base');
    git(repo, 'tag', '-a', 'v1.0.0', base, '-m', 'base');
    const patchSha = commit(repo, 'patch change');
    const minorSha = commit(repo, 'minor change');
    git(repo, 'push', 'origin', 'main', '--tags');

    const bodies = new Map([
      [patchSha, { number: 11, body: '+semver: patch' }],
      [minorSha, { number: 12, body: '+semver: minor' }],
    ]);
    global.fetch = async url => {
      const parts = String(url).split('/');
      const commitSha = parts[parts.indexOf('commits') + 1];
      const item = bodies.get(commitSha);
      const payload = item ? [{ ...item, merged_at: '2026-08-19T00:00:00Z', base: { ref: 'main' } }] : [];
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'content-type': 'application/json' } });
    };

    const result = await policy.resolveMain({ repository: 'owner/repo', head: minorSha, branch: 'main', token: 'test', cwd: repo });
    assert.deepEqual(result.intents, ['patch', 'minor']);
    assert.equal(result.bump, 'minor');
    assert.equal(result.version, '1.1.0');

    const none = policy.resolutionObject({
      baseTag: 'v1.1.0',
      baseVersion: { major: 1, minor: 1, patch: 0 },
      bump: 'none',
      version: { major: 1, minor: 1, patch: 0 },
      sourceSha: minorSha,
      intents: ['none'],
    });
    assert.equal(policy.createAnnotatedTag(none, repo), 'no-tag');
  } finally {
    global.fetch = originalFetch;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('annotated tag allocation is idempotent and never invents a release', () => {
  const { root, remote, repo } = setupRepo();
  try {
    const sha = commit(repo, 'change');
    git(repo, 'push', 'origin', 'main');
    const resolution = policy.resolutionObject({
      baseTag: 'v1.0.0',
      baseVersion: { major: 1, minor: 0, patch: 0 },
      bump: 'patch',
      version: { major: 1, minor: 0, patch: 1 },
      sourceSha: sha,
      intents: ['patch'],
    });

    assert.equal(policy.createAnnotatedTag(resolution, repo), 'created');
    assert.equal(policy.createAnnotatedTag(resolution, repo), 'already-exists');
    assert.equal(git(repo, 'cat-file', '-t', 'refs/tags/v1.0.1'), 'tag');
    assert.equal(git(repo, 'rev-list', '-n', '1', 'v1.0.1'), sha);
    assert.match(execFileSync('git', ['--git-dir', remote, 'tag', '--list'], { encoding: 'utf8' }), /v1\.0\.1/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('superseded main candidate is not eligible for a tag', async () => {
  const { root, repo } = setupRepo();
  const originalFetch = global.fetch;
  try {
    const oldSha = commit(repo, 'old');
    git(repo, 'push', 'origin', 'main');
    commit(repo, 'new');
    git(repo, 'push', 'origin', 'main');
    global.fetch = async () => new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } });

    const result = await policy.resolveMain({ repository: 'owner/repo', head: oldSha, branch: 'main', token: 'test', cwd: repo });
    assert.equal(result.superseded, true);
    assert.equal(result.tag_required, false);
  } finally {
    global.fetch = originalFetch;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

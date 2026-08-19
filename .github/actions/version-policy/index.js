'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const INTENT_RE = /^\s*\+semver:\s*(major|minor|patch|none)\s*$/gim;
const TAG_RE = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const BUMP_RANK = { none: 0, patch: 1, minor: 2, major: 3 };

class VersionPolicyError extends Error {}

function input(name, fallback = '') {
  // GitHub exposes action inputs as INPUT_<NAME>, uppercasing and replacing
  // spaces with underscores while preserving punctuation such as hyphens.
  const key = `INPUT_${name.replace(/ /g, '_').toUpperCase()}`;
  const value = process.env[key];
  return value === undefined || value === '' ? fallback : value;
}

function parseVersion(value) {
  const text = String(value || '').trim();
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(text);
  if (!match) {
    throw new VersionPolicyError(`not a canonical semantic version: ${JSON.stringify(text)}`);
  }
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) };
}

function parseTag(tag) {
  const match = TAG_RE.exec(String(tag || '').trim());
  if (!match) {
    throw new VersionPolicyError(`not a canonical version tag: ${JSON.stringify(tag)}`);
  }
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) };
}

function semver(version) {
  return `${version.major}.${version.minor}.${version.patch}`;
}

function tag(version) {
  return `v${semver(version)}`;
}

function bumpVersion(version, intent) {
  switch (intent) {
    case 'none': return { ...version };
    case 'patch': return { major: version.major, minor: version.minor, patch: version.patch + 1 };
    case 'minor': return { major: version.major, minor: version.minor + 1, patch: 0 };
    case 'major': return { major: version.major + 1, minor: 0, patch: 0 };
    default: throw new VersionPolicyError(`unsupported semver intent: ${JSON.stringify(intent)}`);
  }
}

function explicitIntents(text) {
  const values = [];
  const source = String(text || '');
  INTENT_RE.lastIndex = 0;
  let match;
  while ((match = INTENT_RE.exec(source)) !== null) {
    values.push(match[1].toLowerCase());
  }
  return values;
}

function parseExactIntent(text) {
  const values = explicitIntents(text);
  if (values.length === 0) {
    throw new VersionPolicyError('exactly one version intent is required: add one line containing +semver: major|minor|patch|none');
  }
  if (values.length !== 1) {
    throw new VersionPolicyError('exactly one version intent is required; duplicate or conflicting +semver directives are not allowed');
  }
  return values[0];
}

function highestBump(intents) {
  const values = [...intents].map(value => String(value).toLowerCase());
  if (values.length === 0) return 'none';
  for (const value of values) {
    if (!(value in BUMP_RANK)) {
      throw new VersionPolicyError(`unsupported semver intent: ${JSON.stringify(value)}`);
    }
  }
  return values.reduce((best, value) => BUMP_RANK[value] > BUMP_RANK[best] ? value : best, 'none');
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    encoding: 'utf8',
    env: options.env || process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error) throw new VersionPolicyError(`${command} failed to launch: ${result.error.message}`);
  const code = result.status === null ? 1 : result.status;
  if (options.check !== false && code !== 0) {
    const detail = String(result.stderr || result.stdout || '').trim() || 'no output';
    throw new VersionPolicyError(`command failed (${[command, ...args].join(' ')}): ${detail}`);
  }
  return { code, stdout: String(result.stdout || ''), stderr: String(result.stderr || '') };
}

function git(args, options = {}) {
  return run('git', args, options);
}

function revParse(ref, cwd = process.cwd()) {
  return git(['rev-parse', ref], { cwd }).stdout.trim();
}

function latestReachableTag(head = 'HEAD', cwd = process.cwd(), baseVersion = '0.0.0') {
  const lines = git(['tag', '--merged', head, '--list', 'v*', '--sort=-v:refname'], { cwd }).stdout.split(/\r?\n/);
  for (const raw of lines) {
    const candidate = raw.trim();
    if (TAG_RE.test(candidate)) return { baseTag: candidate, baseVersion: parseTag(candidate) };
  }
  return { baseTag: '', baseVersion: parseVersion(baseVersion) };
}

async function githubJson(url, token) {
  if (!token) throw new VersionPolicyError('github-token is required for resolve-main mode');
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'autodev-version-policy',
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new VersionPolicyError(`GitHub API request failed (${response.status}) for ${url}: ${text.slice(0, 500)}`);
  }
  return response.json();
}

async function associatedPulls(repository, commit, token) {
  const apiUrl = process.env.GITHUB_API_URL || 'https://api.github.com';
  const payload = await githubJson(`${apiUrl}/repos/${repository}/commits/${commit}/pulls`, token);
  if (!Array.isArray(payload)) {
    throw new VersionPolicyError(`GitHub returned an unexpected PR association payload for commit ${commit.slice(0, 12)}`);
  }
  return payload;
}

function resolutionObject({ baseTag, baseVersion, bump, version, sourceSha, intents, superseded = false }) {
  return {
    base_tag: baseTag,
    base_version: semver(baseVersion),
    bump,
    version: semver(version),
    tag: tag(version),
    source_sha: sourceSha,
    tag_required: !superseded && bump !== 'none',
    superseded,
    tag_status: 'not-requested',
    intents: [...intents],
  };
}

function candidateForPr({ body, head = 'HEAD', cwd = process.cwd(), baseVersion = '0.0.0' }) {
  const intent = parseExactIntent(body);
  const base = latestReachableTag(head, cwd, baseVersion);
  const sourceSha = revParse(head, cwd);
  return resolutionObject({
    ...base,
    bump: intent,
    version: bumpVersion(base.baseVersion, intent),
    sourceSha,
    intents: [intent],
  });
}

async function resolveMain({ repository, head, branch = 'main', token, cwd = process.cwd(), baseVersion = '0.0.0' }) {
  git(['fetch', 'origin', branch, '--tags', '--force'], { cwd });
  const remoteHead = revParse(`origin/${branch}`, cwd);
  const sourceSha = revParse(head, cwd);
  const base = latestReachableTag(sourceSha, cwd, baseVersion);
  if (remoteHead !== sourceSha) {
    return resolutionObject({
      ...base,
      bump: 'none',
      version: base.baseVersion,
      sourceSha,
      intents: [],
      superseded: true,
    });
  }

  const range = base.baseTag ? `${base.baseTag}..${sourceSha}` : sourceSha;
  const commits = git(['rev-list', '--reverse', range], { cwd }).stdout.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  const intents = [];
  const seenPulls = new Set();

  for (const commit of commits) {
    const pulls = await associatedPulls(repository, commit, token);
    const merged = pulls.filter(item => item && item.merged_at && item.base && item.base.ref === branch);
    if (merged.length > 0) {
      for (const pull of merged) {
        const number = Number(pull.number || 0);
        if (!number || seenPulls.has(number)) continue;
        seenPulls.add(number);
        const values = explicitIntents(pull.body || '');
        if (values.length > 1) {
          throw new VersionPolicyError(`merged PR #${number} contains duplicate/conflicting +semver directives`);
        }
        if (values.length === 1) intents.push(values[0]);
      }
      continue;
    }

    const message = git(['show', '-s', '--format=%B', commit], { cwd }).stdout;
    const values = explicitIntents(message);
    if (values.length > 1) {
      throw new VersionPolicyError(`direct main commit ${commit.slice(0, 12)} contains duplicate/conflicting +semver directives`);
    }
    if (values.length === 1) intents.push(values[0]);
  }

  const selected = highestBump(intents);
  return resolutionObject({
    ...base,
    bump: selected,
    version: bumpVersion(base.baseVersion, selected),
    sourceSha,
    intents,
  });
}

function annotatedTagType(tagName, cwd) {
  const result = git(['cat-file', '-t', `refs/tags/${tagName}`], { cwd, check: false });
  return result.code === 0 ? result.stdout.trim() : '';
}

function existingTagCommit(tagName, cwd) {
  const result = git(['rev-parse', '-q', '--verify', `refs/tags/${tagName}^{commit}`], { cwd, check: false });
  return result.code === 0 ? result.stdout.trim() : '';
}

function createAnnotatedTag(resolution, cwd = process.cwd()) {
  if (resolution.superseded) return 'superseded';
  if (!resolution.tag_required) return 'no-tag';

  const tagName = resolution.tag;
  const sourceSha = resolution.source_sha;
  const existing = existingTagCommit(tagName, cwd);
  if (existing) {
    if (existing !== sourceSha) {
      throw new VersionPolicyError(`refusing to move existing tag ${tagName}: it points to ${existing}, not ${sourceSha}`);
    }
    const type = annotatedTagType(tagName, cwd);
    if (type !== 'tag') {
      throw new VersionPolicyError(`refusing lightweight/non-annotated existing version tag ${tagName}; expected annotated tag`);
    }
    return 'already-exists';
  }

  git(['tag', '-a', tagName, sourceSha, '-m', `Version ${resolution.version}`], { cwd });
  const pushed = git(['push', 'origin', `refs/tags/${tagName}`], { cwd, check: false });
  if (pushed.code === 0) return 'created';

  git(['fetch', 'origin', '--tags', '--force'], { cwd });
  const remote = existingTagCommit(tagName, cwd);
  if (remote === sourceSha && annotatedTagType(tagName, cwd) === 'tag') return 'concurrent-identical';
  throw new VersionPolicyError(`failed to push version tag ${tagName}: ${(pushed.stderr || pushed.stdout || 'no output').trim()}`);
}

function writeOutput(name, value) {
  const outputPath = process.env.GITHUB_OUTPUT;
  if (!outputPath) return;
  const rendered = Array.isArray(value) ? JSON.stringify(value) : typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value ?? '');
  fs.appendFileSync(outputPath, `${name}=${rendered}\n`, 'utf8');
}

function emitOutputs(resolution) {
  for (const [key, value] of Object.entries(resolution)) writeOutput(key, value);
}

function summary(resolution) {
  return `base=${resolution.base_tag || '(none)'} bump=${resolution.bump} version=${resolution.version} tag=${resolution.tag} required=${resolution.tag_required} superseded=${resolution.superseded}`;
}

function booleanInput(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
}

async function main() {
  const mode = input('mode').trim();
  const cwd = process.cwd();
  const head = input('head', process.env.GITHUB_SHA || 'HEAD').trim() || 'HEAD';
  const baseVersion = input('base-version', '0.0.0').trim();
  let resolution;

  if (mode === 'check-pr') {
    resolution = candidateForPr({ body: input('pr-body'), head, cwd, baseVersion });
  } else if (mode === 'resolve-main') {
    const repository = input('repository', process.env.GITHUB_REPOSITORY || '').trim();
    if (!repository || !repository.includes('/')) {
      throw new VersionPolicyError('repository must be supplied as owner/name (or GITHUB_REPOSITORY must be set)');
    }
    resolution = await resolveMain({
      repository,
      head,
      branch: input('branch', 'main').trim() || 'main',
      token: input('github-token', process.env.GITHUB_TOKEN || ''),
      cwd,
      baseVersion,
    });
    if (booleanInput(input('create-tag', 'false'))) {
      resolution.tag_status = createAnnotatedTag(resolution, cwd);
    }
  } else {
    throw new VersionPolicyError(`unsupported mode ${JSON.stringify(mode)}; expected check-pr or resolve-main`);
  }

  emitOutputs(resolution);
  console.log(summary(resolution));
  if (resolution.tag_status !== 'not-requested') console.log(`tag_status=${resolution.tag_status}`);
}

if (require.main === module) {
  main().catch(error => {
    const message = error && error.message ? error.message : String(error);
    console.error(`Version policy error: ${message}`);
    process.exitCode = 2;
  });
}

module.exports = {
  VersionPolicyError,
  parseVersion,
  parseTag,
  semver,
  bumpVersion,
  explicitIntents,
  parseExactIntent,
  highestBump,
  latestReachableTag,
  candidateForPr,
  resolveMain,
  createAnnotatedTag,
  resolutionObject,
};

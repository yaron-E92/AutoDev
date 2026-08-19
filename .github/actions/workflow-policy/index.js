'use strict';

const fs = require('fs');
const path = require('path');

const FULL_SHA = /^[0-9a-f]{40}$/i;
const WORKFLOW_EXTENSIONS = new Set(['.yml', '.yaml']);

function input(name, fallback = '') {
  const envName = `INPUT_${name.replace(/ /g, '_').replace(/-/g, '_').toUpperCase()}`;
  return process.env[envName] ?? fallback;
}

function boolInput(name, fallback = false) {
  const value = input(name, String(fallback)).trim().toLowerCase();
  if (['true', '1', 'yes', 'on'].includes(value)) return true;
  if (['false', '0', 'no', 'off', ''].includes(value)) return false;
  throw new Error(`${name} must be true or false, got ${value}`);
}

function workflowFiles(root) {
  if (!fs.existsSync(root)) return [];
  const results = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(candidate);
      else if (entry.isFile() && WORKFLOW_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        results.push(candidate);
      }
    }
  }
  return results.sort();
}

function usesReferences(text) {
  const refs = [];
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:-\s*)?uses:\s*([^\s#]+)\s*(?:#.*)?$/);
    if (match) refs.push(match[1].trim().replace(/^['"]|['"]$/g, ''));
  }
  return refs;
}

function isImmutableExternalReference(reference) {
  if (!reference || reference.startsWith('./') || reference.startsWith('$/') || reference.startsWith('docker://')) {
    return true;
  }
  const at = reference.lastIndexOf('@');
  if (at <= 0 || at === reference.length - 1) return false;
  return FULL_SHA.test(reference.slice(at + 1));
}

function isReusableWorkflow(text) {
  return /^\s{0,2}workflow_call\s*:/m.test(text);
}

function hasTopLevelKey(text, key) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped}:`, 'm').test(text);
}

function validateWorkflow(file, text, options = {}) {
  const errors = [];
  const refs = usesReferences(text);
  for (const ref of refs) {
    if (!isImmutableExternalReference(ref)) {
      errors.push(`${file}: mutable or invalid external uses ref: ${ref}`);
    }
  }

  if (/^\s*permissions:\s*write-all\s*$/m.test(text)) {
    errors.push(`${file}: permissions: write-all is forbidden`);
  }
  if (!hasTopLevelKey(text, 'permissions')) {
    errors.push(`${file}: missing explicit top-level permissions`);
  }

  if (options.requireConcurrency && !isReusableWorkflow(text) && !hasTopLevelKey(text, 'concurrency')) {
    errors.push(`${file}: missing explicit top-level concurrency`);
  }

  return errors;
}

function validateDirectory(root, options = {}) {
  const files = workflowFiles(root);
  const errors = [];
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    errors.push(...validateWorkflow(file, text, options));
  }
  return { files, errors };
}

function writeOutput(name, value) {
  const output = process.env.GITHUB_OUTPUT;
  if (!output) return;
  fs.appendFileSync(output, `${name}=${value}\n`, 'utf8');
}

function main() {
  const root = input('workflows-path', '.github/workflows');
  const requireConcurrency = boolInput('require-concurrency', false);
  const { files, errors } = validateDirectory(root, { requireConcurrency });
  writeOutput('checked_files', files.length);

  console.log(`Checked ${files.length} workflow file(s) under ${root}.`);
  if (errors.length) {
    for (const error of errors) console.error(`::error::${error}`);
    process.exitCode = 1;
  }
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(`::error::${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}

module.exports = {
  boolInput,
  hasTopLevelKey,
  isImmutableExternalReference,
  isReusableWorkflow,
  usesReferences,
  validateDirectory,
  validateWorkflow,
  workflowFiles,
};

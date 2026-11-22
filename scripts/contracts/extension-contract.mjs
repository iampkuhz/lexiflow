import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const ts = createRequire(new URL('../../extension/package.json', import.meta.url))('typescript');
const java = 'backend/product/api/src/main/java/io/lexiflow/api/';
const lexicon = 'backend/product/enrichment/src/main/java/io/lexiflow/enrichment/application/preferences/';
const http = [
  ['runtime/RuntimeStatusController.java', '/api/v1/runtime-status', 'GetMapping'],
  ['hints/CaptionHintController.java', '/api/v1/caption-hints', 'PostMapping'],
  ['preferences/PreferencesController.java', '/api/v1/preferences', 'PostMapping'],
  ['preferences/RecentHintsController.java', '/api/v1/recent-hints', 'PostMapping'],
  ['lexicon/LexiconBrowseController.java', '/api/v1/lexicon', 'GetMapping'],
];
const javaModels = [
  java + 'runtime/RuntimeStatus.java', java + 'hints/model/CaptionHintRequest.java',
  java + 'hints/model/CaptionHintResponse.java', lexicon + 'LocalPreferences.java',
];
const extension = {
  'protocol.ts': ['PreviousSegment', 'CaptionSegment', 'CaptionSnapshot', 'CaptionHintRequest', 'KeyedHint', 'HintResponse',
    'MAX_CAPTION_LENGTH', 'MAX_PRESENTED_ENTRY_IDS', 'exactFields', 'boundedSegmentList', 'isCaptionSnapshot',
    'isCaptionHintRequest', 'isEntryId', 'isFrequencyRank', 'boundedId', 'nonnegative', 'splitsSurrogate', 'resolveHint',
    'parseHintResponse', 'snapshotSegments'],
  'runtime-status.ts': ['PublishedDataset', 'RuntimeStatus', 'datasetKeys', 'reasons', 'validDataset', 'validVersion', 'parseRuntimeStatus'],
  'preferences.ts': ['HiddenPreference', 'Preferences', 'RecentItem', 'isHiddenPreference', 'isCutoff', 'parsePreferences'],
  'background.ts': ['PreferenceOperation', 'RecentOperation', 'LexiconItem', 'parseLexicon', 'parseRecent'],
};

function scan(source) {
  const scanner = ts.createScanner(ts.ScriptTarget.ESNext, true, ts.LanguageVariant.Standard, source);
  const tokens = [];
  for (let kind; (kind = scanner.scan()) !== ts.SyntaxKind.EndOfFileToken;) tokens.push(scanner.getTokenText());
  return tokens;
}

function close(tokens, from) {
  const end = { '(': ')', '[': ']', '{': '}' }[tokens[from]];
  if (!end) throw new Error('WIRE_DELIMITER_UNKNOWN');
  let depth = 1;
  for (let i = from + 1; i < tokens.length; i++) {
    if (tokens[i] === tokens[from]) depth++;
    else if (tokens[i] === end && --depth === 0) return i;
  }
  throw new Error('WIRE_DELIMITER_UNCLOSED');
}

// Only these reply records have an already-tolerant receiver. Request and status DTOs are closed.
const openReplies = new Set(['lexicon/LexiconBrowseController.java:Item', 'lexicon/LexiconBrowseController.java:Response',
  'preferences/RecentHintsController.java:Item', 'preferences/RecentHintsController.java:Result']);
function recordFields(tokens, row) {
  const end = close(tokens, row + 2), fields = [];
  let part = [], generic = 0;
  for (let i = row + 3; i <= end; i++) {
    const token = tokens[i];
    if (token === '(' || token === '[' || token === '{') {
      const stop = close(tokens, i);
      part.push(...tokens.slice(i, stop + 1)); i = stop; continue;
    }
    if (token === '<') generic++;
    if (/^>+$/u.test(token)) generic -= token.length;
    if (i === end || (token === ',' && generic === 0)) { if (part.length) fields.push(part); part = []; }
    else part.push(token);
  }
  return fields;
}

function javaProjection(text, endpoint) {
  const tokens = scan(text);
  const records = [], annotations = [], calls = [], states = [];
  for (let i = 0; i < tokens.length; i++) {
    const value = tokens[i];
    if (value === 'record' && tokens[i + 2] === '(') {
      const name = tokens[i + 1];
      const fields = recordFields(tokens, i).filter(field => {
        if (!field.includes('WireAdvisory')) return true;
        if (!openReplies.has(endpoint?.[0] + ':' + name)) throw new Error('WIRE_ADVISORY_ON_CLOSED_MESSAGE');
        if (field[0] !== '@' || field[1] !== 'WireAdvisory' ||
            /^(?:boolean|byte|short|int|long|float|double|char)$/u.test(field[2]))
          throw new Error('WIRE_ADVISORY_MUST_BE_NULLABLE');
        return false;
      });
      records.push([name, fields]);
    }
    if (tokens[i - 1] === '@' && /^(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestParam|RequestHeader|RequestBody|ResponseStatus|WireSemantics)$/.test(value)) {
      const end = tokens[i + 1] === '(' ? close(tokens, i + 1) : i;
      annotations.push(tokens.slice(i, end + 1));
    }
    if ((value === 'requireKeys' || (value === 'of' && tokens[i-1] === '.' && tokens[i-2] === 'Set')) && tokens[i + 1] === '(' && tokens[i + 2] !== 'Map' && tokens[i + 2] !== 'java')
      calls.push(tokens.slice(i, close(tokens, i + 1) + 1));
    if (value === 'case' && tokens[i + 1]?.startsWith('"')) states.push(tokens[i + 1]);
    if (value === 'HttpStatus' && tokens[i + 1] === '.') states.push(tokens[i + 2]);
    if (value === 'ResponseEntity' && tokens[i + 1] === '.' && tokens[i + 3] === '(') states.push(tokens[i + 2]);
  }
  if (endpoint && (!annotations.some(a => a.some(v => /^"\/api\/v[0-9]+\//u.test(v))) ||
      !annotations.some(a => /^(Get|Post|Put|Delete|Patch|Request)Mapping$/u.test(a[0])))) throw new Error('WIRE_ENDPOINT_UNRECOGNIZED: ' + endpoint[0]);
  if (!endpoint && !records.length) throw new Error('WIRE_MODEL_UNRECOGNIZED');
  return { records, annotations, calls, states: [...new Set(states)].sort() };
}

function typescriptProjection(filename, text, names) {
  const source = ts.createSourceFile(filename, text, ts.ScriptTarget.ESNext, true);
  if (source.parseDiagnostics.length) throw new Error('WIRE_TYPESCRIPT_INVALID: ' + filename);
  const selected = new Map();
  for (const statement of source.statements) {
    const values = ts.isVariableStatement(statement) ? statement.declarationList.declarations : [statement];
    for (const value of values) if (value.name && names.includes(value.name.getText(source))) {
      // Walking parsed tokens preserves regexp literals and ignores comments and formatting.
      const leaves = node => ts.isToken(node) ? [node.getText(source)] : node.getChildren(source).flatMap(leaves);
      const representation = leaves(value);
      selected.set(value.name.getText(source), representation);
    }
  }
  if (selected.size !== names.length) throw new Error('WIRE_VALIDATOR_UNRECOGNIZED: ' + filename);
  return names.map(name => selected.get(name));
}

export function extensionContract(root) {
  const read = name => readFileSync(join(root, name), 'utf8');
  const httpFacts = http.map(endpoint => javaProjection(read(java + endpoint[0]), endpoint));
  const requestPolicy = read('backend/product/api/src/main/resources/application.yaml')
    .match(/^\s*fail-on-unknown-properties:\s*(true|false)\s*$/mu)?.[1];
  if (!requestPolicy) throw new Error('WIRE_REQUEST_POLICY_UNRECOGNIZED');
  return {
    format: 1,
    http: httpFacts,
    requestPolicy,
    models: javaModels.map(file => javaProjection(read(file))),
    validators: Object.entries(extension).map(([file, names]) => typescriptProjection(file, read('extension/src/' + file), names)),
  };
}

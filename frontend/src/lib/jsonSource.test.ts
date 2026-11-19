import { expect, it } from 'vitest'
import { formatJsonSource } from './jsonSource'

it('indents JSON without changing exact numbers, string escapes, duplicate keys, or opaque values', () => {
  const source = '{"large":9007199254740993,"decimal":1.2300e+30,"value":null,"value":false,"escaped":"a\\nb","list":[1,2]}'
  const result = formatJsonSource(source)
  expect(result.formatted).toBe(true)
  expect(result.text).toBe([
    '{',
    '  "large": 9007199254740993,',
    '  "decimal": 1.2300e+30,',
    '  "value": null,',
    '  "value": false,',
    '  "escaped": "a\\nb",',
    '  "list": [',
    '    1,',
    '    2',
    '  ]',
    '}',
  ].join('\n'))
})

it('retains malformed or non-JSON source text for inspection', () => {
  for (const source of ['{"missing":', '{"trailing":true,}', 'not JSON', '']) {
    expect(formatJsonSource(source)).toEqual({ text: source, formatted: false })
  }
})

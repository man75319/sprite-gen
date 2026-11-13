// The locked spec as form fields (docs/char.md section 2). Choices mirror the CLI's; the CLI checks them again.
import type { Spec } from './api.ts';
import { h } from './ui.ts';

export const CHOICES: Record<string, string[]> = {
  provider: ['dmx', 'xai'],
  resolution: ['480p', '720p', '1080p'],
  facing: ['right', 'left'],
  key: ['green', 'magenta'],
  body_plan: ['biped', 'quadruped', 'legless'],
  walk_start: ['as-given', 'redraw'],
};

export const LABELS: Record<string, string> = {
  provider: 'provider',
  model: 'model',
  resolution: 'resolution',
  facing: 'facing',
  key: 'key（建后不可改）',
  body_plan: 'body plan',
  body_height: 'body height（px，必填）',
  fps: 'fps（空 = 按片源）',
  walk_start: 'walk start',
};

export const DEFAULTS: Partial<Spec> = {
  provider: 'dmx',
  model: 'doubao-seedance-2-0-260128',
  resolution: '480p',
  facing: 'right',
  key: 'green',
  body_plan: 'biped',
  walk_start: 'as-given',
};

/** One labelled input per spec field, named as the field (the server maps names to flags). */
export function specFields(spec: Partial<Spec>, opts: { lockKey: boolean }): HTMLElement[] {
  return Object.keys(LABELS).map((field) => {
    const value = (spec as Record<string, unknown>)[field];
    let input: HTMLElement;
    if (CHOICES[field]) {
      const opts2 = [...CHOICES[field]];
      if (typeof value === 'string' && !opts2.includes(value)) opts2.push(value);
      input = h('select', { name: field, disabled: field === 'key' && opts.lockKey },
        ...opts2.map((o) => h('option', { value: o, selected: o === value }, o)));
    } else if (field === 'body_height' || field === 'fps') {
      input = h('input', { name: field, type: 'number', min: field === 'fps' ? 0 : 1, step: field === 'fps' ? 'any' : 1,
        value: value === null || value === undefined ? '' : String(value), required: field === 'body_height' });
    } else {
      input = h('input', { name: field, type: 'text', value: value === null || value === undefined ? '' : String(value) });
    }
    return h('label', {}, h('span', {}, LABELS[field]), input);
  });
}

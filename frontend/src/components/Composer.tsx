import { useState, type FormEvent } from 'react';
import type { PlanInput, TaskType } from '../types';

const availableFields = ['title', 'price', 'category', 'description'];
const demoUrl = 'https://demo.sanyojan.test/products';

interface Props {
  busy: boolean;
  onPlan: (input: PlanInput) => Promise<void>;
}

export function Composer({ busy, onPlan }: Props) {
  const [instruction, setInstruction] = useState('');
  const [targetUrl, setTargetUrl] = useState(demoUrl);
  const [taskType, setTaskType] = useState<TaskType>('extraction');
  const [fields, setFields] = useState(['title', 'price', 'category']);
  const [validation, setValidation] = useState<string | null>(null);

  function useExample(kind: 'demo' | 'public') {
    setInstruction(kind === 'demo'
      ? 'Extract the title, price, and category of every product from this demo catalog.'
      : 'Extract the title and price of the books listed on this public catalog page.');
    setTargetUrl(kind === 'demo' ? demoUrl : 'https://books.toscrape.com/');
    setTaskType('extraction');
    setFields(kind === 'demo' ? ['title', 'price', 'category'] : ['title', 'price']);
    setValidation(null);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = instruction.trim();
    if (text.length < 10 || text.length > 2000) {
      setValidation('Describe your task in 10–2,000 characters.');
      return;
    }
    try {
      const url = new URL(targetUrl.trim() || demoUrl);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid protocol');
    } catch {
      setValidation('Enter a valid http:// or https:// target URL.');
      return;
    }
    setValidation(null);
    void onPlan({
      instruction: text,
      target_url: targetUrl.trim() || demoUrl,
      task_type: taskType,
      ...(fields.length ? { fields } : {}),
    });
  }

  return (
    <section className="panel composer" aria-labelledby="composer-title">
      <div className="panel-heading">
        <span className="section-number">01</span>
        <div><h2 id="composer-title">Start with an intent</h2><p>Tell your agent what to do.</p></div>
      </div>
      <form onSubmit={submit} noValidate>
        <fieldset disabled={busy} className="composer-fields">
          <label htmlFor="instruction">Your instruction <span className="label-aside">Natural language</span></label>
          <textarea
            id="instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="e.g. Extract product names, prices, and categories from this catalog…"
            rows={5}
            maxLength={2000}
            aria-describedby="instruction-hint"
          />
          <div id="instruction-hint" className="input-hint"><span>Be specific about the outcome.</span><span>{instruction.length}/2,000</span></div>
          <div className="example-row"><span>Try an example</span><button type="button" onClick={() => useExample('demo')}>Demo catalog <span aria-hidden="true">↗</span></button><button type="button" onClick={() => useExample('public')}>Public books <span aria-hidden="true">↗</span></button></div>

          <label htmlFor="target-url">Target URL</label>
          <div className="url-input"><span aria-hidden="true">↗</span><input id="target-url" type="url" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} placeholder={demoUrl} /></div>

          <label htmlFor="task-type">Task type</label>
          <select id="task-type" value={taskType} onChange={(event) => {
            const next = event.target.value as TaskType;
            setTaskType(next);
            setFields(next === 'inspection' ? ['title', 'description'] : ['title', 'price']);
          }}>
            <option value="extraction">Data extraction</option>
            <option value="inspection">Page inspection</option>
            <option value="report">Generate report</option>
          </select>

          <fieldset className="field-picker">
            <legend>Output fields <span className="label-aside">Optional</span></legend>
            <div className="field-options">
              {availableFields.filter((field) => taskType !== 'inspection' || ['title', 'description'].includes(field)).map((field) => (
                <label className={`field-option ${fields.includes(field) ? 'checked' : ''}`} key={field}>
                  <input type="checkbox" checked={fields.includes(field)} onChange={() => setFields((previous) => previous.includes(field) ? previous.filter((item) => item !== field) : [...previous, field])} />
                  <span className="field-check" aria-hidden="true">{fields.includes(field) ? '✓' : '+'}</span>{field}
                </label>
              ))}
            </div>
            <p className="help-text">None selected? Fields are inferred from your instruction. Inspection supports title and description.</p>
          </fieldset>
          {validation && <div className="notice error" role="alert">{validation}</div>}
          <button className="button primary full-width" type="submit">{busy ? <><span className="spinner" /> Creating your plan…</> : <>Generate a plan <span aria-hidden="true">→</span></>}</button>
          <p className="approval-note"><span className="mini-shield" aria-hidden="true">✓</span> Nothing runs until you approve the plan.</p>
        </fieldset>
      </form>
    </section>
  );
}

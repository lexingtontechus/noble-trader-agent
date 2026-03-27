'use client'
import styles from './Field.module.css'

export default function Field({ label, children }) {
  return (
    <div className={styles.field}>
      <label className={styles.label}>{label}</label>
      {children}
    </div>
  )
}

export function FieldInput({ label, ...props }) {
  return (
    <Field label={label}>
      <input className={styles.input} {...props} />
    </Field>
  )
}

export function FieldSelect({ label, options, ...props }) {
  return (
    <Field label={label}>
      <select className={styles.input} {...props}>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </Field>
  )
}

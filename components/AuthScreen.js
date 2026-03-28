"use client";
import { SignIn } from "@clerk/nextjs";
import styles from "./AuthScreen.module.css";

const clerkAppearance = {
  variables: {
    colorPrimary: "#c8f542",
    colorBackground: "#13151a",
    colorText: "#eef0f4",
    colorTextSecondary: "#6b7280",
    colorInputBackground: "#1c1f27",
    colorInputText: "#eef0f4",
    borderRadius: "8px",
  },
  elements: {
    card: {
      background: "#13151a",
      border: "1px solid rgba(255,255,255,0.12)",
      boxShadow: "none",
    },
    headerTitle: { color: "#eef0f4", fontWeight: "700" },
    headerSubtitle: { color: "#6b7280" },
    formButtonPrimary: {
      background: "#c8f542",
      color: "#0b0c0f",
      fontWeight: "700",
    },
    formFieldInput: {
      background: "#1c1f27",
      border: "1px solid rgba(255,255,255,0.15)",
      color: "#eef0f4",
    },
    footerActionLink: { color: "#c8f542" },
    dividerLine: { background: "rgba(255,255,255,0.07)" },
  },
};

export default function AuthScreen() {
  return (
    <div className={styles.screen}>
      <div className={styles.backdrop} />
      <div className={styles.content}>
        <div className={styles.brand}>
          <div className={styles.logoMark}>
            <svg viewBox="0 0 20 20" fill="none">
              <path
                d="M3 14L7 9L11 11L17 5"
                stroke="#0b0c0f"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="17" cy="5" r="2" fill="#0b0c0f" />
            </svg>
          </div>
          <div className="max-w-lg py-4">
            <h1 className={styles.brandName}>Noble Trader Agent</h1>
            <p className={styles.brandSub}>Risk Manager</p>
            <p className={styles.brandSub}>By Noble Trading App</p>
          </div>
        </div>

        <p className={styles.tagline}>Dynamic Risk + Regime Engine</p>
        <div className="max-w-lg">
          <div className={styles.features}>
            {[
              {
                color: "var(--accent)",
                text: "Probability-weighted position sizing",
              },
              { color: "var(--blue)", text: "Regime detection" },
              {
                color: "var(--green)",
                text: "Real-time drawdown protection",
              },
              {
                color: "var(--amber)",
                text: "Risk overlay & volatility normalization",
              },
            ].map(({ color, text }) => (
              <div key={text} className={styles.featureItem}>
                <span
                  className={styles.featureDot}
                  style={{ background: color }}
                />
                {text}
              </div>
            ))}
          </div>
        </div>

        <div className="mx-auto">
          <div className={styles.clerkWrap}>
            <SignIn appearance={clerkAppearance} />
          </div>
        </div>
      </div>
      <div className={styles.grid} aria-hidden="true">
        {Array.from({ length: 120 }).map((_, i) => (
          <div key={i} className={styles.gridCell} />
        ))}
      </div>
    </div>
  );
}

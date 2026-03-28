"use client";
import styles from "../components/AuthScreen.module.css";
import { Show } from "@clerk/nextjs";
import RiskManagerClient from "./riskmanager/RiskManagerClient";
import AuthScreen from "../components/AuthScreen";

export default function Home() {
  return (
    <div className="text-center">
      <Show when="signed-out">
        <AuthScreen />
      </Show>
      <Show when="signed-in">
        <RiskManagerClient />
      </Show>
    </div>
  );
}

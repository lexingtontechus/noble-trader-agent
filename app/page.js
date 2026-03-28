"use client";

import { Show } from "@clerk/nextjs";
import RiskManagerClient from "./riskmanager/RiskManagerClient";
//import AuthScreen from "../components/AuthScreen";

export default function Home() {
  return (
    <div className="">
      <Show when="signed-out">Risk Manager</Show>
      <Show when="signed-in">
        <RiskManagerClient />
      </Show>
    </div>
  );
}

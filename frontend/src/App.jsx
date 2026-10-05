import { useEffect, useState } from 'react';
import { startSimulation } from './state/simulation.js';
import { useIntro, useRevealOnScroll } from './hooks.js';
import { getUser, onAuthChange } from './lib/api.js';
import LoginScreen from './components/LoginScreen.jsx';
import Navbar from './components/Navbar.jsx';
import TwinStage from './components/TwinStage.jsx';
import FleetBar from './components/FleetBar.jsx';
import Kpis from './components/Kpis.jsx';
import Inspector from './components/Inspector.jsx';
import FleetHealth from './components/FleetHealth.jsx';
import HealthHeatmap from './components/HealthHeatmap.jsx';
import TrendChart from './components/TrendChart.jsx';
import NeedsAttention from './components/NeedsAttention.jsx';
import Alerts from './components/Alerts.jsx';
import MaintenancePlan from './components/MaintenancePlan.jsx';
import Tooltip from './components/Tooltip.jsx';
import Footer from './components/Footer.jsx';

function Console() {
  // Mounted only while signed in, so the simulation — its 3 s poll and its socket — starts
  // with a session and tears down on sign-out instead of retrying into a 401.
  useEffect(() => startSimulation(), []); // one simulated cycle every 1.2 s
  useIntro();
  useRevealOnScroll();

  return (
    <>
      <main className="wrap">
        <Navbar />
        <TwinStage />
        <FleetBar />
        <Kpis />
        <Inspector />
        <FleetHealth />
        <HealthHeatmap />
        <TrendChart />
        <NeedsAttention />
        <Alerts />
        <MaintenancePlan />
      </main>
      <Tooltip />
      <Footer />
    </>
  );
}

export default function App() {
  // `undefined` means "not read yet": sessionStorage has been consulted by the module that
  // holds the token, so this is safe to decide during the first render.
  const [user, setUser] = useState(() => getUser());

  useEffect(() => onAuthChange(setUser), []);

  if (!user) return <LoginScreen onSignedIn={setUser} />;
  return <Console key={user.id ?? user.username ?? 'user'} />;
}
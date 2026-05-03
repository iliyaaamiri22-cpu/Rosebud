import { useState, useEffect, useRef } from "react";
import "@/App.css";
import { motion, useInView } from "framer-motion";
import Marquee from "react-fast-marquee";
import axios from "axios";
import {
  Bot, Zap, Mail, CreditCard, ChevronRight, Circle,
  Terminal, ExternalLink, ArrowRight, CheckCircle2, Clock
} from "lucide-react";
import { FaTelegram } from "react-icons/fa";
import { SiStripe } from "react-icons/si";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

// Terminal Typing Animation Component
const TerminalWindow = () => {
  const lines = [
    { delay: 0, text: "$ /gen_checkout", type: "cmd" },
    { delay: 800, text: "⏳ Generating temp email...", type: "info" },
    { delay: 1400, text: "📧 Email: k7x2m9a4p1@1secmail.com", type: "success" },
    { delay: 2000, text: "🌐 Signing up on Rosebud.ai...", type: "info" },
    { delay: 2800, text: "✅ Magic link received!", type: "success" },
    { delay: 3400, text: "💳 Extracting Stripe checkout...", type: "info" },
    { delay: 4200, text: "🔗 https://checkout.stripe.com/pay/cs_live_...", type: "link" },
    { delay: 5000, text: "✅ Done! Checkout ready.", type: "done" },
  ];

  const [visibleLines, setVisibleLines] = useState([]);

  useEffect(() => {
    const timers = lines.map((line) =>
      setTimeout(() => {
        setVisibleLines(prev => [...prev, line]);
      }, line.delay)
    );
    const reset = setTimeout(() => setVisibleLines([]), 6500);
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(reset);
    };
  }, []);

  const typeColors = {
    cmd: "#00FF94",
    info: "#A1A1AA",
    success: "#00F0FF",
    link: "#FACC15",
    done: "#00FF94",
  };

  return (
    <div data-testid="terminal-window" className="terminal-window">
      <div className="terminal-header">
        <span className="terminal-dot red" />
        <span className="terminal-dot yellow" />
        <span className="terminal-dot green" />
        <span className="terminal-title">rosebud-bot — bash</span>
      </div>
      <div className="terminal-body">
        {visibleLines.map((line, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3 }}
            className="terminal-line"
            style={{ color: typeColors[line.type] }}
          >
            {line.type === "cmd" && <span className="terminal-prompt">❯ </span>}
            {line.text}
            {i === visibleLines.length - 1 && <span className="cursor-blink">▋</span>}
          </motion.div>
        ))}
      </div>
    </div>
  );
};

// Status Card
const BotStatusCard = ({ stats }) => (
  <motion.div
    data-testid="bot-status-card"
    className="status-card"
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.6, delay: 0.4 }}
  >
    <div className="status-header">
      <div className="status-dot-wrapper">
        <span className="status-pulse" />
        <span className="status-dot-inner" />
      </div>
      <span className="status-label">Bot Online</span>
    </div>
    <div className="status-grid">
      <div className="status-stat">
        <div data-testid="total-checkouts" className="stat-value">{stats?.total_checkouts ?? "—"}</div>
        <div className="stat-label">Total Generated</div>
      </div>
      <div className="status-stat">
        <div data-testid="success-checkouts" className="stat-value success-val">{stats?.successful_checkouts ?? "—"}</div>
        <div className="stat-label">Successful</div>
      </div>
    </div>
  </motion.div>
);

// How It Works Step Card
const StepCard = ({ number, icon: Icon, title, desc, delay }) => {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true });

  return (
    <motion.div
      ref={ref}
      data-testid={`step-card-${number}`}
      className="step-card"
      initial={{ opacity: 0, y: 30 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.6, delay }}
    >
      <div className="step-number">0{number}</div>
      <div className="step-icon-wrap">
        <Icon size={24} />
      </div>
      <h3 className="step-title">{title}</h3>
      <p className="step-desc">{desc}</p>
    </motion.div>
  );
};

// Recent Checkouts Table
const CheckoutsTable = ({ data }) => (
  <motion.div
    data-testid="checkouts-table"
    className="checkouts-table-wrap"
    initial={{ opacity: 0 }}
    whileInView={{ opacity: 1 }}
    transition={{ duration: 0.8 }}
    viewport={{ once: true }}
  >
    <div className="table-header-row">
      <div className="table-title">Recent Checkouts</div>
      <div className="table-badge">{data.length} entries</div>
    </div>
    <div className="table-scroll">
      {data.length === 0 ? (
        <div className="table-empty">No checkouts yet — run /gen_checkout in the bot!</div>
      ) : (
        data.map((row, i) => (
          <div key={i} className="table-row" data-testid={`table-row-${i}`}>
            <div className="table-cell email">{row.email}</div>
            <div className="table-cell link">
              <a href={row.checkout_url} target="_blank" rel="noreferrer">
                View Checkout <ExternalLink size={12} />
              </a>
            </div>
            <div className="table-cell time">
              <Clock size={12} />
              {new Date(row.created_at).toLocaleTimeString()}
            </div>
          </div>
        ))
      )}
    </div>
  </motion.div>
);

function App() {
  const [stats, setStats] = useState(null);
  const [checkouts, setCheckouts] = useState([]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [s, c] = await Promise.all([
          axios.get(`${API}/status`),
          axios.get(`${API}/checkouts`)
        ]);
        setStats(s.data);
        setCheckouts(c.data);
      } catch (e) {
        console.error("API error:", e);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app-root">
      {/* HERO */}
      <section className="hero-section">
        <div className="hero-bg-overlay" />
        <div className="hero-content">
          <motion.div
            className="hero-badge"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5 }}
            data-testid="hero-badge"
          >
            <FaTelegram size={14} /> Telegram Bot &nbsp;•&nbsp; Auto Checkout
          </motion.div>

          <motion.h1
            className="hero-title"
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
          >
            One Command.<br />
            <span className="hero-accent">Instant Checkout.</span>
          </motion.h1>

          <motion.p
            className="hero-subtitle"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.2 }}
          >
            Auto signup on Rosebud.ai with temp mail + generate Stripe checkout link.<br />
            One command. Zero hassle.
          </motion.p>

          <motion.div
            className="hero-cta-row"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.3 }}
          >
            <a
              data-testid="telegram-btn"
              href="https://t.me/"
              target="_blank"
              rel="noreferrer"
              className="btn-primary"
            >
              <FaTelegram size={18} /> Open in Telegram
            </a>
            <div className="cmd-chip" data-testid="command-chip">
              <Terminal size={14} />
              <code>/gen_checkout</code>
            </div>
          </motion.div>

          <div className="hero-grid">
            <TerminalWindow />
            <BotStatusCard stats={stats} />
          </div>
        </div>
      </section>

      {/* MARQUEE STRIP */}
      <div className="marquee-strip">
        <Marquee speed={50} gradient={false} autoFill>
          {["TEMP MAIL", "AUTO SIGNUP", "STRIPE CHECKOUT", "ROSEBUD.AI", "ONE COMMAND", "INSTANT RESULTS"].map((t, i) => (
            <span key={i} className="marquee-item">
              <span className="marquee-dot" />
              {t}
            </span>
          ))}
        </Marquee>
      </div>

      {/* HOW IT WORKS */}
      <section className="how-section" id="how-it-works">
        <div className="section-container">
          <motion.div
            className="section-label"
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
          >
            How It Works
          </motion.div>
          <motion.h2
            className="section-title"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            From Command to Checkout in 90 Seconds
          </motion.h2>

          <div className="steps-grid">
            <StepCard number={1} icon={Mail} title="Temp Email Generated"
              desc="A unique disposable email is created instantly using 1secmail API." delay={0} />
            <StepCard number={2} icon={Bot} title="Auto Signup"
              desc="Bot signs up on Rosebud.ai, receives the magic link, and logs in automatically." delay={0.15} />
            <StepCard number={3} icon={SiStripe} title="Stripe Checkout"
              desc="Navigates to the pricing page and extracts the Stripe checkout URL for the lowest plan." delay={0.3} />
            <StepCard number={4} icon={Zap} title="Link Delivered"
              desc="The checkout link is sent directly to you in Telegram. Open it and complete purchase." delay={0.45} />
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section className="features-section">
        <div className="section-container">
          <div className="features-grid">
            <motion.div
              className="feature-big-card"
              initial={{ opacity: 0, x: -30 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true }}
            >
              <div className="feature-icon-big">
                <FaTelegram size={40} />
              </div>
              <h3>Single Command</h3>
              <p>Just type <code>/gen_checkout</code> in Telegram. The bot handles everything — from email generation to login to checkout extraction.</p>
              <div className="feature-tags">
                <span>Automated</span>
                <span>Instant</span>
                <span>Reliable</span>
              </div>
            </motion.div>

            <div className="feature-small-grid">
              {[
                { icon: CheckCircle2, title: "No Manual Signup", desc: "100% automated registration flow with Playwright browser automation." },
                { icon: Mail, title: "Disposable Emails", desc: "Fresh temp email for every request. No spam, no tracking." },
                { icon: CreditCard, title: "Lowest Plan", desc: "Always targets the lowest available Stripe subscription plan." },
                { icon: Clock, title: "Fast Delivery", desc: "Checkout link delivered within 60-120 seconds of command." },
              ].map((f, i) => (
                <motion.div
                  key={i}
                  data-testid={`feature-card-${i}`}
                  className="feature-small-card"
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.1 }}
                >
                  <f.icon size={20} className="feature-small-icon" />
                  <div>
                    <div className="feature-small-title">{f.title}</div>
                    <div className="feature-small-desc">{f.desc}</div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* RECENT CHECKOUTS */}
      <section className="history-section">
        <div className="section-container">
          <motion.div className="section-label" initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}>
            Activity Log
          </motion.div>
          <motion.h2 className="section-title" initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}>
            Recent Generated Checkouts
          </motion.h2>
          <CheckoutsTable data={checkouts} />
        </div>
      </section>

      {/* FOOTER */}
      <footer className="footer">
        <div className="footer-inner">
          <div className="footer-brand">
            <Bot size={20} />
            <span>RosebudBot</span>
          </div>
          <div className="footer-cmd" data-testid="footer-cmd">
            <Terminal size={14} />
            <code>/gen_checkout</code>
          </div>
          <div className="footer-right">
            <SiStripe size={16} />
            <FaTelegram size={16} />
          </div>
        </div>
      </footer>
    </div>
  );
}

export default App;

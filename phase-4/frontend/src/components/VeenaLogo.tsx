import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';

export const VeenaLogo: React.FC<{ className?: string; activeString?: number }> = ({ 
  className = "w-32 h-auto",
  activeString = null 
}) => {
  const strings = [
    { id: 1, base: "#1E2A5E", active: "#065f46" }, // Matsya
    { id: 2, base: "#1E2A5E", active: "#c2410c" }, // Varaha
    { id: 3, base: "#1E2A5E", active: "#c2410c" }, // Narasimha
    { id: 4, base: "#1E2A5E", active: "#2d2a26" }, // Rama
    { id: 5, base: "#1E2A5E", active: "#065f46" }, // Krishna
    { id: 6, base: "#1E2A5E", active: "#fcd34d" }, // Buddha
    { id: 7, base: "#1E2A5E", active: "#57534e" }, // Parashurama
    { id: 8, base: "#1E2A5E", active: "#78716c" }, // Vamana
  ];

  const [agents, setAgents] = useState<{id: number, strId: number, color: string}[]>([]);

  // When activeString changes, shoot an agent
  useEffect(() => {
    if (activeString !== null) {
      const color = strings.find(s => s.id === activeString)?.active || "#c2410c";
      const newAgent = { id: Date.now(), strId: activeString, color };
      setAgents(prev => [...prev, newAgent]);
      // Remove it after animation
      setTimeout(() => {
        setAgents(prev => prev.filter(a => a.id !== newAgent.id));
      }, 1500);
    }
  }, [activeString]);

  // If no string is manually active, randomly pluck one every few seconds to show life
  useEffect(() => {
    if (activeString !== null) return;
    const interval = setInterval(() => {
      const randomStr = Math.floor(Math.random() * 8) + 1;
      const color = strings.find(s => s.id === randomStr)?.active || "#c2410c";
      const newAgent = { id: Date.now(), strId: randomStr, color };
      setAgents(prev => [...prev, newAgent]);
      setTimeout(() => {
        setAgents(prev => prev.filter(a => a.id !== newAgent.id));
      }, 1500);
    }, 4000);
    return () => clearInterval(interval);
  }, [activeString]);

  return (
    <svg viewBox="-50 -10 300 420" className={className} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <pattern id="madhubani-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="4" stroke="#c2410c" strokeWidth="0.5" />
        </pattern>
        <pattern id="madhubani-dots" width="8" height="8" patternUnits="userSpaceOnUse">
          <circle cx="4" cy="4" r="1.5" fill="#2d2a26" opacity="0.4" />
        </pattern>
      </defs>

      {/* Decorative Outer Aura / Lotus Petals in Madhubani style */}
      <motion.path 
        d="M-20 150 C -40 100, 0 50, 50 100 C 100 50, 140 100, 120 150 C 180 180, 180 280, 120 310 C 140 370, 80 400, 50 350 C 20 400, -40 370, -20 310 C -80 280, -80 180, -20 150 Z" 
        fill="url(#madhubani-dots)" fillOpacity="0.5" stroke="#fcd34d" strokeWidth="2" strokeDasharray="3 3"
        animate={{ rotate: 360 }}
        transition={{ duration: 120, repeat: Infinity, ease: "linear" }}
        style={{ transformOrigin: "50px 230px" }}
      />

      <path d="M 50,40 Q -10,120 50,200 Q 110,120 50,40 Z" fill="url(#madhubani-hatch)" opacity="0.3" transform="translate(50, 120)" />
      
      {/* Kesari Halo */}
      <circle cx="100" cy="320" r="80" fill="#fcfaf2" stroke="#c2410c" strokeWidth="6" strokeDasharray="4 8" />

      {/* Main Gourds - Madhubani Style */}
      {/* Top Gourd (daanda) */}
      <circle cx="100" cy="64" r="45" fill="#fcfaf2" stroke="#2d2a26" strokeWidth="4" />
      {/* Decorative lotus inside top gourd */}
      <path d="M 100 30 C 115 50, 120 60, 100 70 C 80 60, 85 50, 100 30 Z" fill="#065f46" stroke="#2d2a26" strokeWidth="2"/>
      <path d="M 100 70 C 115 80, 120 90, 100 100 C 80 90, 85 80, 100 70 Z" fill="#c2410c" stroke="#2d2a26" strokeWidth="2"/>
      
      {/* Bottom Gourd (tumba) */}
      <circle cx="100" cy="320" r="64" fill="#fcfaf2" stroke="#2d2a26" strokeWidth="4" />
      
      {/* Inner detailing bottom gourd - Madhubani Leaves & Triangles */}
      <path d="M 100 264 L 108 276 L 92 276 Z M 100 376 L 108 364 L 92 364 Z M 44 320 L 56 312 L 56 328 Z M 156 320 L 144 312 L 144 328 Z" fill="#2d2a26" />
      <circle cx="100" cy="320" r="50" fill="none" stroke="#c2410c" strokeWidth="2" strokeDasharray="1 4"/>
      
      {/* Elaborate Central Rosette in bottom gourd */}
      <circle cx="100" cy="320" r="24" fill="#fcd34d" stroke="#2d2a26" strokeWidth="2" />
      <circle cx="100" cy="320" r="14" fill="#065f46" stroke="#2d2a26" strokeWidth="1" />

      {/* Stem (dandi) */}
      <rect x="74" y="64" width="52" height="256" fill="#fcfaf2" stroke="#2d2a26" strokeWidth="4" />
      
      {/* Frets (8 strings means 8 frets/marks) */}
      {Array.from({length: 8}).map((_, i) => (
        <line key={`fret-${i}`} x1="74" y1={100 + i * 25} x2="126" y2={100 + i * 25} stroke="#2d2a26" strokeWidth="2" strokeDasharray="2 2" strokeOpacity="0.6" />
      ))}

      {/* 8 Strings & Plucking Animation */}
      {strings.map((str, i) => {
        const isActive = activeString === str.id || agents.some(a => a.strId === str.id);
        const xPos = 80 + i * 5.7; // Spaced evenly across stem width of 52
        
        return (
          <motion.path 
            key={`str-${str.id}`}
            stroke={isActive ? str.active : str.base} 
            strokeWidth={isActive ? "2.5" : "1.5"}
            fill="none"
            initial={{ d: `M ${xPos} 30 Q ${xPos} 185 ${xPos} 340` }}
            animate={{ 
              d: isActive 
                ? [
                    `M ${xPos} 30 Q ${xPos} 185 ${xPos} 340`, // base
                    `M ${xPos} 30 Q ${xPos - 40} 185 ${xPos} 340`, // pull far left
                    `M ${xPos} 30 Q ${xPos + 20} 185 ${xPos} 340`, // snap right
                    `M ${xPos} 30 Q ${xPos - 5} 185 ${xPos} 340`, // vibrate left
                    `M ${xPos} 30 Q ${xPos} 185 ${xPos} 340` // return
                  ]
                : `M ${xPos} 30 Q ${xPos} 185 ${xPos} 340`
            }}
            transition={{
              duration: 0.6,
              ease: "backOut",
            }}
          />
        );
      })}

      {/* Releasing Agents (Orbs shooting up) */}
      <AnimatePresence>
        {agents.map(agent => (
          <motion.g 
            key={agent.id}
            initial={{ x: 100, y: 185, scale: 0, opacity: 1 }}
            animate={{ 
              x: 100 + (Math.random() * 80 - 40), // slightly random sideways 
              y: -50, 
              scale: [0, 1.5, 0.8], 
              opacity: [1, 1, 0] 
            }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.2, ease: "easeOut" }}
          >
            {/* The Agent - Looks like a glowing Madhubani eye/orb */}
            <circle cx="0" cy="0" r="8" fill={agent.color} />
            <circle cx="0" cy="0" r="4" fill="#fcfaf2" />
            <circle cx="0" cy="0" r="1.5" fill="#2d2a26" />
            {/* Sparkles trailing */}
            <line x1="-8" y1="0" x2="-14" y2="0" stroke={agent.color} strokeWidth="1.5" strokeLinecap="round" />
            <line x1="8" y1="0" x2="14" y2="0" stroke={agent.color} strokeWidth="1.5" strokeLinecap="round" />
          </motion.g>
        ))}
      </AnimatePresence>

      {/* Bindu - The beating heart / Narad's Presence */}
      <motion.circle 
        cx="100" cy="320" r="6" 
        fill="#c2410c" 
        animate={{ scale: [1, 1.5, 1], opacity: [1, 0.7, 1] }} 
        transition={{ repeat: Infinity, duration: 2, ease: "easeInOut" }} 
      />
      
      {/* Top Headstock flair - Madhubani Crown */}
      <path d="M 85 20 C 85 -15, 115 -15, 115 20 L 100 35 Z" fill="#fcfaf2" stroke="#2d2a26" strokeWidth="3" />
      <circle cx="100" cy="0" r="6" fill="#c2410c" stroke="#2d2a26" strokeWidth="2" />
      <line x1="100" y1="-6" x2="100" y2="-16" stroke="#2d2a26" strokeWidth="2" />
      <circle cx="100" cy="-18" r="2" fill="#2d2a26" />

    </svg>
  );
};

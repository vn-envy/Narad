import React from 'react';
import { motion } from 'motion/react';

export const MadhubaniBorder: React.FC<{ className?: string, position?: 'top' | 'bottom' }> = ({ className = "", position = 'top' }) => {
  return (
    <div className={`w-full overflow-hidden bg-paper border-kajal ${position === 'top' ? 'border-b-[3px]' : 'border-t-[3px]'} ${className}`}>
      <svg width="100%" height="40" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="madhubani-band" x="0" y="0" width="160" height="40" patternUnits="userSpaceOnUse">
             {/* Border lines */}
             <line x1="0" y1="2" x2="160" y2="2" stroke="#2d2a26" strokeWidth="1.5" />
             <line x1="0" y1="6" x2="160" y2="6" stroke="#2d2a26" strokeWidth="0.5" />
             <line x1="0" y1="34" x2="160" y2="34" stroke="#2d2a26" strokeWidth="0.5" />
             <line x1="0" y1="38" x2="160" y2="38" stroke="#2d2a26" strokeWidth="1.5" />

             {/* Peacocks and Fishes alternating */}
             <g transform="translate(10, 8)">
               {/* Simplified Fish */}
               <path d="M 0 12 C 10 0, 20 0, 30 12 C 20 24, 10 24, 0 12 Z" fill="#065f46" stroke="#2d2a26" strokeWidth="1" />
               <circle cx="22" cy="10" r="1.5" fill="#fcd34d" />
               <path d="M -5 6 L 0 12 L -5 18 Z" fill="#c2410c" stroke="#2d2a26" strokeWidth="0.5"/>
             </g>

             {/* Floral Lotus Box */}
             <rect x="50" y="8" width="24" height="24" fill="none" stroke="#c2410c" strokeWidth="1" />
             <path d="M 62 10 L 68 20 L 56 20 Z" fill="#fcd34d" stroke="#2d2a26" strokeWidth="1"/>
             <path d="M 62 30 L 68 20 L 56 20 Z" fill="#c2410c" stroke="#2d2a26" strokeWidth="1"/>
             
             <g transform="translate(90, 8)">
               {/* Simplified Peacock / Bird */}
               <path d="M 10 16 C 5 16, 2 10, 5 4 C 15 4, 15 16, 25 16 C 30 16, 30 20, 25 20 C 10 20, 10 24, 5 24 Z" fill="#065f46" stroke="#2d2a26" strokeWidth="1" />
               <circle cx="8" cy="8" r="1.5" fill="#fcd34d" />
               <path d="M 15 10 C 20 6, 25 6, 35 12" stroke="#c2410c" strokeWidth="1.5" fill="none" strokeDasharray="2 2" />
               <path d="M 15 14 C 20 10, 25 10, 35 16" stroke="#c2410c" strokeWidth="1.5" fill="none" strokeDasharray="2 2" />
             </g>

             {/* Geometric Triangles */}
             <path d="M 134 8 L 140 20 L 128 20 Z M 134 32 L 140 20 L 128 20 Z" fill="#2d2a26" />
             
             <g transform="translate(144, 16)">
               <circle cx="8" cy="4" r="3" fill="#c2410c" stroke="#2d2a26" strokeWidth="1" />
             </g>
          </pattern>
        </defs>
        
        <motion.rect 
          x="-160" y="0" width="200%" height="40" 
          fill="url(#madhubani-band)" 
          animate={{ x: [0, -160] }}
          transition={{ duration: 10, ease: "linear", repeat: Infinity }}
        />
      </svg>
    </div>
  );
};

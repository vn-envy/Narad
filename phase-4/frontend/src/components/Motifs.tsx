/**
 * Madhubani motif library — one stroke language, all colours from design tokens.
 * Defaults read correctly on paper; pass `color` overrides for dark surfaces.
 */
import React from 'react';

export const ZigzagBank: React.FC<{ color?: string; className?: string }> = ({ color = 'var(--sindoor)', className = '' }) => (
  <svg width="200" height="30" viewBox="0 0 200 30" xmlns="http://www.w3.org/2000/svg" className={className}>
    <path d="M 0 15 L 15 0 L 30 15 L 45 0 L 60 15 L 75 0 L 90 15 L 105 0 L 120 15 L 135 0 L 150 15 L 165 0 L 180 15 L 195 0 L 210 15" fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M 0 30 L 15 15 L 30 30 L 45 15 L 60 30 L 75 15 L 90 30 L 105 15 L 120 30 L 135 15 L 150 30 L 165 15 L 180 30 L 195 15 L 210 30" fill="none" stroke="var(--kajal)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const HalftoneCorner: React.FC<{ className?: string; color?: string }> = ({ className = '', color = 'var(--sindoor)' }) => (
  <svg width="120" height="120" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" className={className}>
    <defs>
      <pattern id="halftone" width="12" height="12" patternUnits="userSpaceOnUse">
        <circle cx="4" cy="4" r="3" fill={color} opacity="0.5" />
      </pattern>
    </defs>
    <path d="M 120 0 L 0 0 C 0 66.274 53.726 120 120 120 L 120 0 Z" fill="url(#halftone)" />
  </svg>
);

export const Squiggle: React.FC<{ className?: string; color?: string }> = ({ className = '', color = 'var(--nila)' }) => (
  <svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg" className={className}>
    <path d="M 5 20 Q 12 5 20 20 T 35 20" fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" />
  </svg>
);

export const DotGrid: React.FC<{ className?: string; color?: string }> = ({ className = '', color = 'var(--kajal)' }) => (
  <svg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg" className={className}>
    {Array.from({ length: 5 }).map((_, i) =>
      Array.from({ length: 5 }).map((_, j) => (
        <circle key={`${i}-${j}`} cx={6 + i * 12} cy={6 + j * 12} r="2" fill={color} opacity="0.8" />
      ))
    )}
  </svg>
);

export const PeacockFeather: React.FC<{ className?: string }> = ({ className = '' }) => (
  <svg width="64" height="120" viewBox="0 0 64 120" xmlns="http://www.w3.org/2000/svg" className={className}>
    <path d="M 32 10 Q 50 40 60 70 Q 32 120 32 120 Q 32 120 4 70 Q 14 40 32 10 Z" fill="var(--tulsi)" stroke="var(--kajal)" strokeWidth="2" />
    <path d="M 32 30 Q 45 50 50 70 Q 32 100 32 100 Q 32 100 14 70 Q 19 50 32 30 Z" fill="var(--mor)" />
    <circle cx="32" cy="65" r="12" fill="var(--kajal)" />
    <circle cx="32" cy="65" r="5" fill="var(--haldi)" />
    <path d="M 32 120 L 32 110" stroke="var(--kajal)" strokeWidth="3" />
  </svg>
);

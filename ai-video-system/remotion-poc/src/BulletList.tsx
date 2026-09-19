import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from './brand';

// 項目を順に出す。これが「変化イベント」を稼ぐ主力になる
export const BulletList: React.FC<{items: string[]}> = ({items}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <AbsoluteFill>
      <div style={{position: 'absolute', left: 160, top: 300}}>
        {items.map((item, i) => {
          const appear = spring({frame: frame - i * 18, fps, config: {damping: 200}});
          return (
            <div key={i} style={{display: 'flex', alignItems: 'center', marginBottom: 40,
                                 opacity: appear, transform: `translateY(${(1 - appear) * 30}px)`}}>
              <div style={{width: 22, height: 22, borderRadius: 11,
                           background: brand.colors.accent2, marginRight: 28}} />
              <div style={{fontFamily: brand.fontFamily, fontSize: 60, fontWeight: 700,
                           color: brand.colors.text}}>{item}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {brand} from './brand';

export const LowerThird: React.FC<{text: string}> = ({text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 200}});
  const x = interpolate(enter, [0, 1], [-420, 0]);
  return (
    <AbsoluteFill>
      <div style={{position: 'absolute', left: 120, bottom: 220,
                   transform: `translateX(${x}px)`, opacity: enter,
                   display: 'flex', alignItems: 'center'}}>
        <div style={{width: 12, height: 84, background: brand.colors.accent}} />
        <div style={{background: brand.colors.surface, padding: '18px 40px',
                     fontFamily: brand.fontFamily, fontSize: 56, fontWeight: 700,
                     color: brand.colors.text}}>{text}</div>
      </div>
    </AbsoluteFill>
  );
};

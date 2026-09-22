import React from 'react';
import {Composition} from 'remotion';
import {GraphicsTrack} from './GraphicsTrack';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="GraphicsTrack"
    component={GraphicsTrack}
    durationInFrames={150}
    fps={30}
    width={1920}
    height={1080}
  />
);

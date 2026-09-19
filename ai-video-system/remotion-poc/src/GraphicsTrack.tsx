import React, {useEffect, useState} from 'react';
import {AbsoluteFill, Sequence, continueRender, delayRender} from 'remotion';
import {brand} from './brand';
import {LowerThird} from './LowerThird';
import {BulletList} from './BulletList';

// 日本語フォントは読み込みを待ってから描画する。
// 待たないとフォント未適用のフレームが混ざる（Remotion+日本語で最頻の事故）
const useJapaneseFont = () => {
  const [handle] = useState(() => delayRender('loading japanese font'));
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const font = new FontFace(brand.fontFamily, `url(${brand.fontFile})`);
    font.load()
      .then((loaded) => {
        document.fonts.add(loaded);
        return document.fonts.ready;
      })
      .then(() => {
        setReady(true);
        continueRender(handle);
      })
      .catch(() => continueRender(handle));
  }, [handle]);
  return ready;
};

export const GraphicsTrack: React.FC = () => {
  useJapaneseFont();
  return (
    // 背景は透明。アルファ付きで書き出して後段で overlay する
    <AbsoluteFill style={{backgroundColor: 'transparent'}}>
      <Sequence from={0} durationInFrames={90}>
        <LowerThird text="日銀 政策金利" />
      </Sequence>
      <Sequence from={45} durationInFrames={105}>
        <BulletList items={['金利差が広がる', '円が売られる', '輸入品が高くなる']} />
      </Sequence>
    </AbsoluteFill>
  );
};

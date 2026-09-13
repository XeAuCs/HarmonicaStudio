import argparse, json, sys
from pathlib import Path
from .models import Options


def main():
    parser=argparse.ArgumentParser(prog='harmonica-studio')
    parser.add_argument('--remote',action='store_true',help='打开桌面界面并显示手机连接二维码')
    sub=parser.add_subparsers(dest='command')
    sub.add_parser('gui',help='打开桌面界面')
    inspect=sub.add_parser('inspect',help='查看音轨信息');inspect.add_argument('file')
    convert=sub.add_parser('convert',help='转换 MIDI');convert.add_argument('file');convert.add_argument('--out',default='data/exports')
    convert.add_argument('--speed',type=float,default=1);convert.add_argument('--transpose',type=int,default=0)
    convert.add_argument('--track',type=int);convert.add_argument('--channel',type=int,help='通道 1–16')
    convert.add_argument('--mode',choices=('sustain','highest'),default='sustain');convert.add_argument('--keep-silence',action='store_true')
    convert.add_argument('--no-auto-octave',action='store_true')
    convert.add_argument('--skip-long-rests',action='store_true',help='将音符之间超过 3 秒的空白缩短为 0.6 秒')
    args=parser.parse_args()
    if args.command in (None,'gui'):
        from .storage import configure_logging
        from .paths import data_root
        configure_logging(data_root()/'logs')
        from .gui import run
        return run(open_remote=args.remote)
    try:
        if args.command=='inspect':
            from .midi import read_midi
            from .melody import rank_parts
            parts,names=read_midi(args.file)
            result=[dict(track=k[0],channel=k[1]+1,name=names.get(k[0],''),notes=len(parts[k]),recommendation_score=round(score,3)) for k,score in rank_parts(parts,names)]
        else:
            if args.channel is not None and not 1<=args.channel<=16:raise ValueError('通道应为 1–16。')
            from .service import convert as export
            options=Options(speed=args.speed,transpose=args.transpose,track=args.track,
                channel=None if args.channel is None else args.channel-1,melody_mode=args.mode,
                trim_silence=not args.keep_silence,auto_octave=not args.no_auto_octave,
                skip_long_rests=args.skip_long_rests)
            folder,report=export(args.file,args.out,options);result=dict(folder=str(folder),report=report)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as exc:
        print('操作失败：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

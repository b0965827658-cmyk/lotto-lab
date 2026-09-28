"""Build review-only LINE menu assets; never calls LINE or uploads anything.

Requires Pillow for local authoring only. Pass --font/--bold-font for other OSes.
"""
import argparse
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ORIGIN='https://lotto-lab-candidate-a-staging.onrender.com'
ITEMS=[
    ('今彩539','官方來源查詢','/start.html#results'),
    ('加州天天樂','Stepzero 第三方','/start.html#fantasy5'),
    ('我要對獎','核對今彩539','/member.html#check'),
    ('我的紀錄','登入後查看','/member.html#history'),
    ('公開留言板','分享想法・互相回覆','/community.html'),
    ('使用說明','資料來源・會員資訊','/help.html'),
]
WELCOME='歡迎來到摘星開獎查詢。點選下方選單，可查今彩539、加州天天樂、對獎與會員紀錄，也能進入公開留言板。今彩539採官方來源；加州天天樂使用Stepzero第三方資料，請核對頁面上的日期與來源。登入並設定公開暱稱後，可分享號碼、查看自己的投稿紀錄及參與留言。'


def build(font,bold_font):
    out=Path(__file__).resolve().parents[1]/'public'/'line-assets'
    out.mkdir(exist_ok=True)
    image=Image.new('RGB',(2500,1686),'#e1e8dc');draw=ImageDraw.Draw(image)
    titlefont=ImageFont.truetype(str(bold_font),108)
    smallfont=ImageFont.truetype(str(font),64)
    iconfont=ImageFont.truetype(str(font),43)
    numberfont=ImageFont.truetype(str(font),32)
    areas=[];xs=[0,833,1667,2500]
    for index,(title,subtitle,path) in enumerate(ITEMS):
        col=index%3;row=index//3;x0,x1=xs[col],xs[col+1];y=row*843
        dark=index in (0,1)
        bg=('#154c3b' if index==0 else '#205d4c') if dark else '#faf9f2'
        ink='#f8f6e9' if dark else '#1d5543';sub='#c6d7c4' if dark else '#637a6b';accent='#d7bc70'
        draw.rounded_rectangle((x0+13,y+13,x1-13,y+830),radius=40,fill=bg)
        draw.text((x0+60,y+53),f'摘星  /  0{index+1}',font=numberfont,fill=sub)
        cx=(x0+x1)//2;cy=y+308
        draw.ellipse((cx-125,cy-125,cx+125,cy+125),outline=accent,width=3)
        if index==0:
            for dx,dy,label in [(-54,18,'5'),(48,18,'9'),(0,-65,'3')]:
                draw.ellipse((cx+dx-34,cy+dy-34,cx+dx+34,cy+dy+34),outline=ink,width=5)
                draw.text((cx+dx,cy+dy-3),label,font=iconfont,anchor='mm',fill=ink)
        elif index==1:
            pts=[]
            for n in range(10):
                angle=-math.pi/2+n*math.pi/5;r=82 if n%2==0 else 36
                pts.append((cx+math.cos(angle)*r,cy+math.sin(angle)*r))
            draw.line(pts+[pts[0]],fill=ink,width=6,joint='curve')
        elif index==2:
            draw.rounded_rectangle((cx-64,cy-83,cx+64,cy+83),radius=15,outline=ink,width=6)
            draw.line([(cx-35,cy),(cx-9,cy+28),(cx+37,cy-27)],fill=ink,width=9,joint='curve')
        elif index==3:
            draw.ellipse((cx-78,cy-78,cx+78,cy+78),outline=ink,width=6)
            draw.line([(cx,cy-51),(cx,cy),(cx+44,cy+27)],fill=ink,width=7,joint='curve')
        elif index==4:
            draw.rounded_rectangle((cx-83,cy-62,cx+83,cy+45),radius=24,outline=ink,width=6)
            draw.line([(cx-33,cy+45),(cx-54,cy+82),(cx+6,cy+45)],fill=ink,width=6)
            for dx in [-41,0,41]:draw.ellipse((cx+dx-6,cy-16,cx+dx+6,cy-4),fill=ink)
        else:
            draw.rounded_rectangle((cx-72,cy-83,cx+72,cy+83),radius=14,outline=ink,width=6)
            for dy,w in [(-35,43),(0,43),(35,24)]:draw.line((cx-43,cy+dy,cx+w,cy+dy),fill=ink,width=6)
        draw.text((cx,y+507),title,font=titlefont,fill=ink,anchor='mm')
        draw.text((cx,y+615),subtitle,font=smallfont,fill=sub,anchor='mm')
        draw.line((cx-30,y+733,cx+30,y+733),fill=accent,width=4)
        areas.append({'bounds':{'x':x0,'y':y,'width':x1-x0,'height':843},
            'action':{'type':'uri','label':title,'uri':ORIGIN+path}})
    image.save(out/'rich-menu.png',optimize=True)
    payload={'size':{'width':2500,'height':1686},'selected':False,
        'name':'摘星 Staging 六格選單草稿','chatBarText':'開獎與會員功能','areas':areas}
    (out/'rich-menu.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'welcome.txt').write_text(WELCOME+'\n',encoding='utf-8')
    assert (out/'rich-menu.png').stat().st_size<1_000_000
    print('Created review-only 2500x1686 PNG, URI payload and welcome text; no network calls.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msjh.ttc'))
    parser.add_argument('--bold-font',type=Path,default=Path('C:/Windows/Fonts/msjhbd.ttc'))
    args=parser.parse_args();build(args.font,args.bold_font)

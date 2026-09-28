"""Build review-only LINE menu assets; never calls LINE or uploads anything.

Requires Pillow for local authoring only. Pass --font/--bold-font for other OSes.
"""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ORIGIN='https://lotto-lab-candidate-a-staging.onrender.com'
ITEMS=[
    ('彩種','選擇彩種・查詢開獎','/start.html',(0,0,2500,562)),
    ('歷史紀錄','登入查看自己的紀錄','/member.html#history',(0,562,1250,562)),
    ('直播專區','台彩直播・港彩直播','/live.html',(1250,562,1250,562)),
    ('公開留言板','分享想法・互相回覆','/community.html',(0,1124,1250,562)),
    ('使用說明','資料來源・會員資訊','/help.html',(1250,1124,1250,562)),
]
WELCOME='歡迎來到摘星。從「彩種」查詢開獎，從「歷史紀錄」查看自己的投稿紀錄，也可進入「直播專區」選擇台彩直播或港彩直播，或到「公開留言板」交流。查詢時請核對彩種、開獎日期與資料來源；直播依節目安排及平台地區限制提供。登入並設定公開暱稱後即可參與留言。'


def build(font,bold_font):
    out=Path(__file__).resolve().parents[1]/'public'/'line-assets'
    out.mkdir(exist_ok=True)
    image=Image.new('RGB',(2500,1686),'#e1e8dc');draw=ImageDraw.Draw(image)
    titlefont=ImageFont.truetype(str(bold_font),108)
    smallfont=ImageFont.truetype(str(font),64)
    numberfont=ImageFont.truetype(str(font),32)
    areas=[]
    for index,(title,subtitle,path,bounds) in enumerate(ITEMS):
        x0,y,width,height=bounds;x1=x0+width
        dark=index==0
        bg='#154c3b' if dark else '#faf9f2'
        ink='#f8f6e9' if dark else '#1d5543';sub='#c6d7c4' if dark else '#637a6b';accent='#d7bc70'
        draw.rounded_rectangle((x0+13,y+13,x1-13,y+height-13),radius=40,fill=bg)
        draw.text((x0+60,y+45),f'摘星  /  0{index+1}',font=numberfont,fill=sub)
        cx=600 if dark else (x0+x1)//2;cy=y+(280 if dark else 196)
        draw.ellipse((cx-105,cy-105,cx+105,cy+105),outline=accent,width=3)
        if index==0:
            for dx,dy in [(-43,28),(43,28),(0,-46)]:
                draw.ellipse((cx+dx-34,cy+dy-34,cx+dx+34,cy+dy+34),outline=ink,width=5)
        elif index==1:
            draw.ellipse((cx-65,cy-65,cx+65,cy+65),outline=ink,width=6)
            draw.line([(cx,cy-44),(cx,cy),(cx+37,cy+23)],fill=ink,width=7,joint='curve')
        elif index==2:
            draw.rounded_rectangle((cx-76,cy-54,cx+76,cy+54),radius=15,outline=ink,width=6)
            draw.polygon([(cx-18,cy-29),(cx-18,cy+29),(cx+32,cy)],fill=ink)
        elif index==3:
            draw.rounded_rectangle((cx-76,cy-55,cx+76,cy+40),radius=24,outline=ink,width=6)
            draw.line([(cx-33,cy+40),(cx-48,cy+72),(cx+6,cy+40)],fill=ink,width=6)
            for dx in [-41,0,41]:draw.ellipse((cx+dx-6,cy-16,cx+dx+6,cy-4),fill=ink)
        else:
            draw.rounded_rectangle((cx-62,cy-70,cx+62,cy+70),radius=14,outline=ink,width=6)
            for dy,w in [(-35,43),(0,43),(35,24)]:draw.line((cx-43,cy+dy,cx+w,cy+dy),fill=ink,width=6)
        text_x=1500 if dark else cx
        draw.text((text_x,y+(225 if dark else 376)),title,font=titlefont,fill=ink,anchor='mm')
        draw.text((text_x,y+(340 if dark else 472)),subtitle,font=smallfont,fill=sub,anchor='mm')
        areas.append({'bounds':{'x':x0,'y':y,'width':width,'height':height},
            'action':{'type':'uri','label':title,'uri':ORIGIN+path}})
    image.save(out/'rich-menu.png',optimize=True)
    payload={'size':{'width':2500,'height':1686},'selected':False,
        'name':'摘星 Staging 五項選單草稿','chatBarText':'彩種・紀錄・直播','areas':areas}
    (out/'rich-menu.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'welcome.txt').write_text(WELCOME+'\n',encoding='utf-8')
    assert (out/'rich-menu.png').stat().st_size<1_000_000
    print('Created review-only 2500x1686 PNG, URI payload and welcome text; no network calls.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msjh.ttc'))
    parser.add_argument('--bold-font',type=Path,default=Path('C:/Windows/Fonts/msjhbd.ttc'))
    args=parser.parse_args();build(args.font,args.bold_font)

## 参数解释

### 用途:

- 获取视频元数据及下载信息
- 此接口收费: 0.002$/次
- 如果需要节省成本，可以使用V2版本，V2版本是0.001$/次，但不保证稳定性。

### 详细参数:

- url\_access:
    - normal: 包含音视频直链
    - blocked: 不包含直链
- videos/audios:
    - auto: 根据url\_access自动选择（normal→true，blocked→false）
    - true: 返回简化格式信息
    - raw: 返回原始格式信息
    - false: 不包含该类型数据

### 返回:

- 视频元数据 + 请求参数对应的资源信息

## 示例请求

```bash
curl -X 'GET' \
  'https://api.tikhub.io/api/v1/youtube/web/get_video_info?video_id=lbESr58-7DQ&url_access=normal&lang=zh-CN&videos=false&audios=auto&subtitles=true&related=false' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer xxx'

```
## 示例响应

```
{
  "code": 200,
  "request_id": "bc084258-936d-49f7-b38d-b58dfaf0b256",
  "message": "Request successful. This request will incur a charge.",
  "message_zh": "请求成功，本次请求将被计费。",
  "support": "Discord: https://discord.gg/aMEAS8Xsvz",
  "time": "2026-01-25 18:40:49",
  "time_stamp": 1769395249,
  "time_zone": "America/Los_Angeles",
  "docs": "https://api.tikhub.io/#/YouTube-Web-API/get_video_info_api_v1_youtube_web_get_video_info_get",
  "cache_message": "This request will be cached. You can access the cached result directly using the URL below, valid for 24 hours. Accessing the cache will not incur additional charges.",
  "cache_message_zh": "本次请求将被缓存，你可以使用下面的 URL 直接访问缓存结果，有效期为 24 小时，访问缓存不会产生额外费用。",
  "cache_url": "https://cache.tikhub.io/api/v1/cache/public/bc084258-936d-49f7-b38d-b58dfaf0b256?sign=5f8488320f90dcc33f4dad70f6b0a55eb5e6ad365c9325a7a5d5c9d483f79fea",
  "router": "/api/v1/youtube/web/get_video_info",
  "params": {
    "video_id": "lbESr58-7DQ",
    "url_access": "normal",
    "lang": "zh-CN",
    "videos": "false",
    "audios": "auto",
    "subtitles": "true",
    "related": "false"
  },
  "data": {
    "errorId": "Success",
    "type": "video",
    "id": "lbESr58-7DQ",
    "title": "Master Google AI Studio in 40 Minutes | Logan Kilpatrick",
    "description": "Logan is the Product Lead for Google AI Studio. I got him to give us an inside look at how he uses AI Studio to build AI Studio (very meta) and how his team ships at startup speed inside Google. AI Studio is the feature that I rely on the most to do my product job better, so don't miss this tutorial.\n\nLogan and I talked about:\n(00:00) Tour of Google AI Studio's Build mode\n(06:29) Live demo: Building a social media content generator\n(09:31) How Logan uses AI Studio to build AI Studio\n(11:34) Cloning the AI Studio UI in 68 seconds\n(12:58) Getting AI to show you 5-6 design options in one UI\n(17:20) Live demo: Building a restaurant finder with Google Maps\n(21:12) Using annotate mode to give visual feedback\n(34:09) \"There's one mode and the mode is we ship fast\"\n(36:00) What Logan looks for when hiring PMs\n\nThanks to our sponsors:\nLinear: The AI agent platform for modern teams https://linear.app/behind-the-craft\nReplit: From 0 to full stack app in 2 min https://replit.com/?utm_source=creator&utm_medium=organic&utm_campaign=creator_program&utm_content=peteryang \n\n📌 Get the takeaways: https://creatoreconomy.so/p/master-google-ai-studio-for-prototyping-logan-kilpatrick\n\nWhere to find Logan:\nX: https://x.com/OfficialLoganK\nWebsite: https://aistudio.google.com/apps\n\nSubscribe to this channel - more interviews coming soon!",
    "channel": {
      "type": "channel",
      "id": "UCnpBg7yqNauHtlNSpOl5-cg",
      "name": "Peter Yang",
      "handle": "@PeterYangYT",
      "isVerified": false,
      "isVerifiedArtist": false,
      "subscriberCountText": "5.09万位订阅者",
      "avatar": [
        {
          "url": "https://yt3.ggpht.com/ytc/AIdro_k0xbR9-CBYMh3YOZJnMQr00qwnbA_aAChW3z0I8lNcGRE=s48-c-k-c0x00ffffff-no-rj",
          "width": 48,
          "height": 48
        },
        {
          "url": "https://yt3.ggpht.com/ytc/AIdro_k0xbR9-CBYMh3YOZJnMQr00qwnbA_aAChW3z0I8lNcGRE=s88-c-k-c0x00ffffff-no-rj",
          "width": 88,
          "height": 88
        },
        {
          "url": "https://yt3.ggpht.com/ytc/AIdro_k0xbR9-CBYMh3YOZJnMQr00qwnbA_aAChW3z0I8lNcGRE=s176-c-k-c0x00ffffff-no-rj",
          "width": 176,
          "height": 176
        }
      ]
    },
    "lengthSeconds": 2358,
    "viewCount": 1996,
    "likeCount": 86,
    "publishedTime": "2026-01-25T06:01:14-08:00",
    "publishedTimeText": "2026年1月25日",
    "isLiveStream": false,
    "isLiveNow": false,
    "isRegionRestricted": false,
    "isUnlisted": false,
    "isCommentDisabled": false,
    "commentCountText": "3",
    "thumbnails": [
      {
        "url": "https://i.ytimg.com/vi/lbESr58-7DQ/hqdefault.jpg?sqp=-oaymwEbCKgBEF5IVfKriqkDDggBFQAAiEIYAXABwAEG&rs=AOn4CLDAcXbzqQO2P-GxseO52A9qXq_CbA",
        "width": 168,
        "height": 94
      },
      {
        "url": "https://i.ytimg.com/vi/lbESr58-7DQ/hqdefault.jpg?sqp=-oaymwEbCMQBEG5IVfKriqkDDggBFQAAiEIYAXABwAEG&rs=AOn4CLB9kNxw12NST8aNgQg2lrf_Ysg4kw",
        "width": 196,
        "height": 110
      },
      {
        "url": "https://i.ytimg.com/vi/lbESr58-7DQ/hqdefault.jpg?sqp=-oaymwEcCPYBEIoBSFXyq4qpAw4IARUAAIhCGAFwAcABBg==&rs=AOn4CLCp8XnzBVyl1BDtnxWRXAmm5tmSCA",
        "width": 246,
        "height": 138
      },
      {
        "url": "https://i.ytimg.com/vi/lbESr58-7DQ/hqdefault.jpg?sqp=-oaymwEcCNACELwBSFXyq4qpAw4IARUAAIhCGAFwAcABBg==&rs=AOn4CLBAn2xBgMKtpwcsS9k6yw0BiCDD3A",
        "width": 336,
        "height": 188
      },
      {
        "url": "https://i.ytimg.com/vi_webp/lbESr58-7DQ/maxresdefault.webp",
        "width": 1920,
        "height": 1080
      }
    ],
    "musicCredits": [],
    "audios": {
      "errorId": "Success",
      "expiration": 1769416848,
      "items": [
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=140&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&mime=audio%2Fmp4&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=38156221&dur=2357.614&lmt=1768982503687035&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRgIhAL9PLIleFPaMjUotl2IOfsCLotQ0iK37E1i1J1MOZQ4vAiEA9sgADBddNk71ojp0bFoNDCyiicmQqk_pnr2dCOC5zuo%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357614,
          "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
          "extension": "m4a",
          "lastModified": 1768982503687035,
          "size": 38156221,
          "sizeText": "36.4MB",
          "isDrc": false
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=140&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&xtags=drc%3D1&mime=audio%2Fmp4&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=38156221&dur=2357.614&lmt=1768983028341948&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cxtags%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRQIgWIlz-1Pu2g4exq-W1jcI0viBeLEiFdkWPq6CpS_SHGoCIQDlw_6rtuNH_rlHclJpGcdWjBMCaR-8jp8m6eMtpur7gQ%3D%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357614,
          "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
          "extension": "m4a",
          "lastModified": 1768983028341948,
          "size": 38156221,
          "sizeText": "36.4MB",
          "isDrc": true
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=249&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=14885771&dur=2357.581&lmt=1768982804544980&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRQIgDHSlnOjyWAihbFKm6pdsYfK2ksK55QIUMMMVkhLytU0CIQDUcG1sTIWTIYnT1qeaNPH99OrdDsXh36RSfIj-Pb2u2w%3D%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982804544980,
          "size": 14885771,
          "sizeText": "14.2MB",
          "isDrc": false
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=249&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&xtags=drc%3D1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=14965895&dur=2357.581&lmt=1768982782006995&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cxtags%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRAIgdIqMYYGLdp2y9uwXwPOIngU6QUKTTdc_OVjBqrFUKcsCIDexicKNvjULvqZ7S9UhpGyD0lspNhWa7qg-EFc0ZYIs&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982782006995,
          "size": 14965895,
          "sizeText": "14.3MB",
          "isDrc": true
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=250&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=17821307&dur=2357.581&lmt=1768982804554582&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRQIgCySDvRweWsSStarzMf6-vsLclRpIHUzNHEW5Jv4OpjwCIQCrVpDIwkjqkWpMA8VPc-9TLOQiicc4w3kfajGUTPqVXw%3D%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982804554582,
          "size": 17821307,
          "sizeText": "17MB",
          "isDrc": false
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=250&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&xtags=drc%3D1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=17837386&dur=2357.581&lmt=1768982781868838&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cxtags%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRAIgTxzMVZO0FINAfq3DEvthvgP-VWJc0RPepszk0XlO_fYCIHt402EzIKFMAooqiKGIXkRuTHYIJSsF3FZkmrJWJaza&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982781868838,
          "size": 17837386,
          "sizeText": "17MB",
          "isDrc": true
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=251&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=31806077&dur=2357.581&lmt=1768982804569078&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRQIgOgl-mqIlrnbIowckrDmiYa1BU-XXjfQLEiJQAHE49v8CIQD-yCQD6_E93tkiz-DQlRyupZRyJVgDqOf2-avRy19O_A%3D%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982804569078,
          "size": 31806077,
          "sizeText": "30.3MB",
          "isDrc": false
        },
        {
          "url": "https://rr1---sn-q4flrnl6.googlevideo.com/videoplayback?expire=1769416848&ei=MNR2aeesNfPfybgPx-CCEQ&ip=2603%3A8080%3Ac409%3Ad825%3Afe49%3A2dff%3Afea4%3Ad616&id=o-AHamm6aV1bvJUHTWV9K48Nwy3XnW-d-0tbJIyL5kgV4T&itag=251&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=25&met=1769395248%2C&mh=vE&mm=31%2C26&mn=sn-q4flrnl6%2Csn-qxoednel&ms=au%2Conr&mv=m&mvi=1&pl=36&rms=au%2Cau&initcwndbps=3232500&bui=AW-iu_pMHphrlHR9tank6AhzUo377R5iQiJXauo2dq_GPWmM_X7antRSvnZwfCMNpAb65R0JgYqbNwsL&spc=q5xjPO6IsdafwnBbTHHQkxa4-L6K-UXBkxADMOmgdXb9XwrKBOpIgwhCaaK4ULD4iH8&vprv=1&svpuc=1&xtags=drc%3D1&mime=audio%2Fwebm&ns=0Tofs7NyZ_bziUk12tciHUES&rqh=1&gir=yes&clen=31863867&dur=2357.581&lmt=1768982781885373&mt=1769394767&fvip=1&keepalive=yes&fexp=51552689%2C51565115%2C51565681%2C51580968&c=WEB_EMBEDDED_PLAYER&sefc=1&txp=6308224&n=z58H-7Tj9nO6rg&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cxtags%2Cmime%2Cns%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AJEij0EwRQIgVQdC66yLvDeA6zIXuC0QEaFaw2Kgl-yNsRD8_aQpUNECIQCQ6qVumS6A97DZXKEu_D9M1ONHZHR3PgPPl8lhZOPn3Q%3D%3D&lsparams=cps%2Cmet%2Cmh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Crms%2Cinitcwndbps&lsig=APaTxxMwRAIgEIPtuyF87f2IdyiVgbAyS113jiXvAPFIvXq3XLPllxYCIEsnBknVWQchpkmZTnm1SHlPszJCT0Hq1TTslnTi7HNU",
          "lengthMs": 2357581,
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "lastModified": 1768982781885373,
          "size": 31863867,
          "sizeText": "30.4MB",
          "isDrc": true
        }
      ]
    },
    "subtitles": {
      "errorId": "Success",
      "expiration": 1769420448,
      "items": [
        {
          "url": "https://www.youtube.com/api/timedtext?v=lbESr58-7DQ&ei=MNR2aeesNfPfybgPx-CCEQ&caps=asr&opi=112496729&xoaf=5&xowf=1&xospf=1&hl=en&ip=0.0.0.0&ipbits=0&expire=1769420448&sparams=ip,ipbits,expire,v,ei,caps,opi,xoaf&signature=D13DDBF1172807C62441BCBE1BC1CACA98D2B64B.9F34DB20F429030C5328972A1BE4A88BE8AF0645&key=yt8&kind=asr&lang=en&variant=ec",
          "code": "en"
        }
      ]
    }
  }
}
```
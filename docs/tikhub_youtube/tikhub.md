## 参数解释

### 用途:

- 获取视频元数据及下载信息
- 此接口收费: 0.002$/次
- 如果需要节省成本，可以使用 V2 版本，V2 版本是 0.001$/次，但不保证稳定性。

### 详细参数:

- url_access:
  - normal: 包含音视频直链
  - blocked: 不包含直链
- videos/audios:
  - auto: 根据 url_access 自动选择（normal→true，blocked→false）
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

## 示例响应（脱敏 + 截断）

```json
{
  "code": 200,
  "message": "Request successful. This request will incur a charge.",
  "cache_url": "https://cache.tikhub.io/api/v1/cache/public/<request_id>?sign=<signature>",
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
    "id": "lbESr58-7DQ",
    "title": "Master Google AI Studio in 40 Minutes | Logan Kilpatrick",
    "channel": {
      "id": "UCnpBg7yqNauHtlNSpOl5-cg",
      "name": "Peter Yang"
    },
    "lengthSeconds": 2358,
    "viewCount": 1996,
    "thumbnails": [
      { "url": "https://i.ytimg.com/vi/lbESr58-7DQ/hqdefault.jpg", "width": 168, "height": 94 },
      { "url": "https://i.ytimg.com/vi_webp/lbESr58-7DQ/maxresdefault.webp", "width": 1920, "height": 1080 }
    ],
    "audios": {
      "errorId": "Success",
      "items": [
        {
          "url": "https://rr1---sn-xxxx.googlevideo.com/videoplayback?...",
          "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
          "extension": "m4a",
          "size": 38156221,
          "sizeText": "36.4MB",
          "isDrc": false
        },
        {
          "url": "https://rr1---sn-xxxx.googlevideo.com/videoplayback?...",
          "mimeType": "audio/webm; codecs=\"opus\"",
          "extension": "weba",
          "size": 14885771,
          "sizeText": "14.2MB",
          "isDrc": false
        }
      ]
    },
    "subtitles": {
      "errorId": "Success",
      "items": [
        {
          "url": "https://www.youtube.com/api/timedtext?...",
          "code": "en"
        }
      ]
    }
  }
}
```

注：
- `audios.items[].url` 和 `subtitles.items[].url` 为短期有效直链，文档中已脱敏。
- `extension` 可能为 `m4a` 或 `weba`（WebM/Opus）。

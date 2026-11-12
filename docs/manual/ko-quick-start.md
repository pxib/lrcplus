# LRCPlus 빠른 시작 안내

이 안내는 LRCPlus를 처음 사용하는 분을 위한 한국어 요약입니다. 전체 기능과 설정은 [영어 사용자 설명서](index.html)를 참고하세요. 앱의 메뉴와 버튼 이름은 실제 화면에서 찾기 쉽도록 영어로 표기합니다.

## 첫 곡 재생하기

1. LRCPlus를 실행합니다. 초기 설정 마법사가 나타나면 안내에 따라 설정합니다.
2. **Open → Open Audio…**를 선택하거나 **Ctrl + O**를 눌러 오디오 파일을 엽니다.
3. **Play**로 재생하거나 일시 정지합니다.
4. **Lyrics**를 선택하면 가사 화면으로, **Library**를 선택하면 음악 목록으로 이동합니다.

자세한 내용은 [Getting started](index.html#getting-started)를 참고하세요.

## 가사 추가하기

가사 파일이 있으면 **Open → Open Lyrics…** 또는 **Ctrl + L**로 엽니다. 온라인에서 찾으려면 **LRC → Search Lyrics**를 선택합니다. `Song.mp3`와 `Song.lrc` 또는 `Song.lrcx`를 같은 폴더에 두고 이름을 맞출 수도 있습니다. 두 가사 형식이 모두 있으면 LRCPlus는 LRCX를 우선합니다.

가사 검색과 적용 방법은 [Finding lyrics](index.html#finding-lyrics)를 참고하세요. 온라인 검색에는 인터넷 연결이 필요합니다.

## 음악 라이브러리와 대기열

**Library**에서 **Add Music Folder**를 눌러 음악 폴더를 추가합니다. 폴더 안의 파일을 변경했다면 **Rescan**을 사용합니다. 재생 대기열에 파일을 추가하려면 **Open → Add to Queue…** 또는 **Ctrl + Shift + O**를 사용합니다. 재생할 목록은 재생 소스 선택기에서 **Library** 또는 **Queue / Playlist**로 고릅니다.

자세한 내용은 [Music and playback](index.html#playback)을 참고하세요.

## 가사 편집과 읽기 보조

가사 텍스트와 타이밍은 [Editing and timing](index.html#editor)에서 설명합니다. 일본어 읽기 보조와 로마자 표기는 [Furigana and readings](index.html#readings)를 참고하세요. 온라인 읽기 제공자를 사용하는 경우 인터넷 연결이 필요합니다.

## 도움이 필요할 때

문제가 생기면 [Troubleshooting](index.html#troubleshooting), 단축키는 [Keyboard shortcuts](index.html#shortcuts), 모든 문서는 [article index](index.html#article-index)를 참고하세요. 앱에서 **Help → User manual**을 선택하거나 **F1**을 눌러 전체 설명서를 열 수 있습니다. 번들된 설명서는 인터넷 연결 없이 읽고 검색할 수 있습니다.

## 가사 검색과 저장

노래를 불러온 후 **Open → Open Lyrics…**에서 `.lrc` 또는 `.lrcx` 파일을 선택합니다. 오디오 파일과 가사 파일의 이름을 맞추면 자동으로 찾는 데 도움이 됩니다. 추가 검색 폴더는 **Settings → Files**에서 관리합니다.

온라인 검색은 노래를 불러오고 **LRC → Search Lyrics**를 선택한 다음 검색 정보를 확인하고 **Search**를 누릅니다. 검색 결과를 선택해 미리보기를 읽고 아티스트와 녹음 버전이 맞는지 확인한 뒤 **Use Selected Lyrics**를 누릅니다. 라이브 버전과 리믹스는 타이밍이 다를 수 있습니다. 일부 제공자는 인증 정보가 필요하며 LRCLIB는 동기화 가사와 일반 텍스트 가사를 지원합니다.

동기화 가사에는 각 줄의 시작 시간이 기록됩니다. 일반 텍스트 가사에는 시간이 없으므로 음악을 따라 움직이지 않습니다. 필요한 경우 가사 편집기에서 시간을 추가할 수 있습니다. **LRC → Save As…**로 가사를 원하는 위치에 저장합니다. 읽기, 노래방 타이밍 또는 서식을 보존하려면 `.lrcx`를 사용하세요.

## 후리가나와 읽기

후리가나는 일본어 가사 옆에 표시되는 작은 읽기 표기이며 로마자는 읽기를 라틴 문자로 표시합니다. LRCX의 루비에는 직접 입력한 읽기도 저장할 수 있습니다.

**Settings → Lyrics**에서 전체 기본값을 바꾸려면 **Global**, 현재 파일에 적용하려면 **Local (File)**을 선택합니다. **Show furigana**, **Show romaji**, **All romaji** 중 하나를 켭니다. 이 모드들은 동시에 사용할 수 없습니다. **Ruby position**으로 후리가나를 가사 위나 아래에 배치합니다.

자동 일본어 파서인 Yomi는 인터넷 연결이 필요합니다. 선택 사항인 로컬 MeCab 파서는 fugashi와 별도로 설치한 사전이 필요합니다. 의존성 정보는 **Settings → Dependencies**에서 확인합니다.

읽기를 생성하려면 **LRC → Edit Lyrics → Generation**에서 **Generate readings for all lyrics** 또는 **Generate For This Timestamp**를 선택합니다. 이름, 특이한 표기와 말장난은 자동 결과가 틀릴 수 있으므로 저장 전에 확인하세요. LRCX에 직접 읽기를 지정하려면 `{base|reading}` 형식을 사용합니다. 예를 들어 `{君|きみ}`입니다.

## 가사 편집과 타이밍

노래를 불러오고 **LRC → Edit Lyrics**를 선택합니다. 타이밍을 크게 변경하기 전에는 별도 사본을 저장하세요. **Lyrics** 탭에서 줄을 선택해 텍스트를 수정하고 **+ Add Line**, **Delete Line**, **Move Up**, **Move Down**으로 줄을 관리합니다. 굵게, 기울임, 취소선, 밑줄과 **Quote** 서식을 사용할 수 있습니다.

줄의 타이밍을 지정하려면 해당 줄을 선택한 후 편집기의 재생 컨트롤로 시작 위치를 찾고 그 순간 **Mark Playback Time**을 누릅니다. 선택한 줄의 시간이 갱신되고 다음 줄로 이동합니다. **Waveform**으로 오디오를 살펴볼 수 있으며 임시 파형 마커를 조정했다면 **Commit**을 눌러 적용합니다. 다시 재생해 가사가 음악을 따라가는지 확인하세요. 일정한 시간 차이는 **Shift Timing…**으로 조정하고 **Validate Timing**으로 타이밍 문제를 확인합니다.

**Karaoke** 탭에서는 **Automatic Separation**으로 구간 경계를 준비하고 확인한 뒤 **Apply Separation**을 누릅니다. 구간을 따라가며 **Mark Playback Time [F]**를 사용합니다. 자동 분리는 정확한 타이밍을 보장하지 않으므로 다시 들어보세요.

**Source** 탭에서는 타이밍이 포함된 가사 원문을 볼 수 있습니다. **Refresh Source**와 **Reload from Source**로 구조화된 편집기와 원문 사이를 이동합니다. 원문을 다시 불러오면 편집 내용이 바뀔 수 있으므로 먼저 사본을 저장하세요. LRCX 기능을 유지하려면 `.lrcx`로 저장합니다.

## 문제 해결

- 소리가 나지 않으면 재생 및 음소거 상태, 플레이어 음량과 Windows 볼륨 믹서를 확인합니다. **Settings → Playback**에서 출력 장치를 **System Default**로 바꾸거나 다른 오디오 파일을 시도합니다.
- 가사가 보이지 않으면 **Lyrics** 화면으로 전환하고 **Ctrl + L**로 가사 파일을 직접 열거나 **LRC → Search Lyrics**를 사용합니다. 파일 이름과 **Settings → Files**의 검색 폴더를 확인합니다. LRC와 LRCX가 모두 있으면 LRCX가 우선합니다.
- 가사 타이밍이 맞지 않으면 녹음 버전이 같은지, 파일에 타임스탬프가 있는지 확인합니다. 일정한 차이는 **Shift Timing…**으로, 곡의 위치에 따라 달라지는 차이는 개별 줄을 수정합니다.
- 읽기가 없거나 틀리면 **Settings → Lyrics**의 읽기 모드와 **Local (File)** 설정을 확인합니다. Yomi에는 연결이, MeCab에는 로컬 패키지와 사전이 필요합니다. 특정 읽기가 필요하면 LRCX 루비를 직접 입력합니다.
- 플러그인이 보이지 않으면 **Settings → Plugins**에서 활성 상태와 로드 오류를 확인하고 **Dependencies**에서 필요한 패키지를 살펴봅니다. 설정 변경 후 필요하면 다시 시작합니다.

문제를 보고할 때는 **Settings → About**의 LRCPlus 버전, Windows 버전, 재현 단계, 예상 결과와 실제 결과를 함께 적습니다. 다른 곡에서도 발생하는지와 선택적 플러그인 사용 여부도 알려주세요.

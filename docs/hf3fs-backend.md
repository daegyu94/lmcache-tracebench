# HF3FS backend (NIXL usrbio)

LMCache L2 backend 중 `nixl_store_dynamic` + `backend: HF3FS`는 NIXL의 HF3FS
플러그인(`libplugin_HF3FS.so`)을 통해 HF3FS(3FS) **usrbio** 드라이버로 직접 I/O를
넣습니다. 일반 3FS backend(`fs_native` + `file_path`)가 HF3FS FUSE mount를 통한
POSIX 파일 I/O를 쓰는 것과 달리, HF3FS backend는 FUSE와 커널 page cache를 우회해
storage node로 user-space RDMA I/O를 수행합니다.

```text
   LMCache L2 replay
        │
        │  nixl_store_dynamic adapter
        ▼
   NIXL agent ── createBackend("HF3FS")
        │
        ▼
   libplugin_HF3FS.so  ← libnixl_common.so, libfile_utils.so
        │
        ▼
   libhf3fs_api_shared.so / hf3fs_usrbio.so   (usrbio driver)
        │  (fuse.hf3fs mount info → usrbio channel)
        ▼
   HF3FS storage node (3fs-virt usrbio channel)
```

일반 3FS FUSE 경로와 마찬가지로 usrbio 역시 HF3FS FUSE mount를 인식해야 합니다.
플러그인은 `hf3fs_extract_mount_point()`로 `/proc/self/mountinfo`에서
`fuse.hf3fs` mount를 찾고 그 mount root의 `3fs-virt` 디렉터리로 usrbio 채널을
엽니다. 따라서 backend 설정에 올바른 **HF3FS mount root**를 `mount_point`로 넘겨야
합니다.

## 설정 파일

`configs/replayer/hf3fs.yaml`:

```yaml
extends: base.yaml

l2_adapter:
  type: nixl_store_dynamic
  backend: HF3FS
  backend_params:
    file_path: /mnt/l2/hf3fs        # LMCache가 보는 L2 namespace (FUSE mount 상의 symlink)
    mount_point: /3fs/stage         # HF3FS FUSE mount root (3fs-virt 디렉터리를 포함)
    use_direct_io: "true"           # NIXL/HF3FS 식의 O_DIRECT (fs_native use_odirect 대응)
    max_capacity_gb: "30720"
```

- `file_path`는 LMCache가 데이터 위치를 기록할 클라이언트-가시 경로입니다. 실제
  storage layout은 `/mnt/l2/hf3fs -> /3fs/stage/l2-nixl` 식의 symlink로 잡습니다.
- `mount_point`는 NIXL HF3FS 플러그인이 읽는 전용 parameter이며 `file_path`와
  무관합니다. `/proc/self/mountinfo`의 `fuse.hf3fs` mount root를 가리켜야 하고,
  mount root 아래에 `3fs-virt` 디렉터리가 존재해야 합니다. 생략하면 플러그인
  default `/mnt/3fs/`를 사용해 이 경로가 존재하지 않을 때
  `boost::filesystem::canonical: No such file or directory`로 실패합니다.
- usrbio 기록은 FUSE namespace에 일반 파일을 남기지 않습니다. 쓰기는
  `{mount_point}/3fs-virt/iovs/{uuid}...` 형태의 transient symlink(iov)를
  `/dev/shm` shared-memory buffer로 매핑하고, storage node가 그 buffer에서
  데이터를 direct-transfer로 읽어 볼륨 chain에 적는 방식입니다. 따라서
  `du -sbL <file_path>`로 측정하는 L2 namespace usage는 HF3FS backend에서
  항상 `0 bytes`로 집계됩니다. 이는 측정 방식의 한계이며 저장 실패를 뜻하지
  않습니다. 실제 저장 여부는 replay summary(`l2_replay_stats.json`)의
  Write/Read op 완료로 확인하고, 저장량은 run 전후 `df`(`hf3fs.*` Used)
  증가분 또는 `admin_cli`의 볼륨 audit으로 관측합니다.

플러그인은 `mount_point` 외에 다음 custom parameter도 인식합니다.

| parameter | 의미 | 기본값 |
| --- | --- | --- |
| `mount_point` | HF3FS FUSE mount root | `/mnt/3fs/` |
| `mem_config` | `dram`, `dram_zc`, `auto` 중 하나 | `auto` |
| `iopool_size` | usrbio I/O pool 크기 | `64` |

## NIXL HF3FS 플러그인 빌드/배포

pip wheel로 설치되는 NIXL(`nixl` / `nixl_cu12` / `nixl_cu13`)에는 `POSIX`, `UCX`,
`GDS`, `GPUNETIO` 등의 플러그인만 포함되고 **`libplugin_HF3FS.so`는 포함되지
않습니다.** HF3FS backend를 쓰려면 NIXL source를 직접 빌드해 이 플러그인을 얻은 뒤
venv의 NIXL plugin 디렉터리에 넣어야 합니다.

빌드는 HF3FS usrbio 개발 header/library가 필요하고, source는 제3자 dependency를
meson subproject(wrap)로 받습니다. 재현 가능한 절차 전체를 다음 script가 수행합니다.

```bash
bash scripts/build_nixl_hf3fs_plugin.sh
```

상세 요구사항과 단계는 아래와 같습니다.

### 요구사항

- NIXL source (예: `ai-dynamo/nixl` tag `v1.3.2`)와 재귀 submodule/subproject
- HF3FS usrbio library/header:
  - `libhf3fs_api_shared.so`, `hf3fs_usrbio.so` (예: `/opt/3fs/lib`)
  - header `hf3fs.h`, `hf3fs_usrbio.h`, `hf3fs_expected.h` (예: `/opt/3fs/include`)
- NIXL의 `src/plugins/hf3fs/meson.build`가 다음 hardcoded 경로로 header/library를 찾습니다.
  - `threefs_inc_path = '/usr/include/hf3fs'`
  - `hf3fs_lib_path = '/usr/lib/'` (상위 `src/plugins/meson.build`)
  - 빌드 전 `/usr/include/hf3fs`, `/usr/lib`에 usrbio header/library를 위치(또는 symlink)시킵니다.
- meson, ninja, C++20 toolchain (`g++`), pybind11 (설치된 venv의 `pybind11/share/pkgconfig`)
- HF3FS mount가 leaf에 존재하고 backend 설정의 `mount_point`가 그것과 일치해야 합니다.

### 빌드 요약

1. NIXL source를 준비합니다. 인터넷이 없는 replay node에서는 controller에서 source와
   meson subproject tarball을 받아 `subprojects/packagecache/`에 넣습니다.
2. `meson setup build -Denable_plugins=HF3FS -Dwerror=false`로 구성합니다.
   `-Dwerror=false`는 HF3FS source의
   `if (&hf3fs_handle->ior == nullptr)` (주소는 항상 non-null) 경고를 `-Werror=address`
   에러로 만들지 않고 넘기기 위한 것입니다. 기본 구성(`werror=true`)에서는 이 한 줄
   때문에 compile이 중단됩니다.
3. `ninja -C build`로 빌드하면 `build/src/plugins/hf3fs/libplugin_HF3FS.so`가 생성됩니다.
4. 생성된 플러그인을 venv의 NIXL plugin 디렉터리로 복사합니다. 실행 시 `nixl_cu12`가
   로드된다면 대상은
   `<venv>/lib/python3.12/site-packages/.nixl_cu12.mesonpy.libs/plugins/` 이고,
   `nixl_cu13`를 쓴다면 그에 대응하는 cu13 경로입니다.
5. 빌드한 플러그인은 설치본과 달리 RPATH가 없어 `libnixl_common.so`,
   `libfile_utils.so`를 못 찾습니다. `patchelf`로 설치본 플러그인과 동일한 RPATH를
   주입합니다.

   ```bash
   patchelf --set-rpath '$ORIGIN/..:$ORIGIN/../../nixl_cu12.libs' \
     <plugin-dir>/libplugin_HF3FS.so
   ```

   `<plugin-dir>`의 상위(`.nixl_cu12.mesonpy.libs`)에 `libnixl_common.so`/
   `libfile_utils.so`가 있고, `nixl_cu12.libs`에 abseil 등의 dependency가 있습니다.
6. `libhf3fs_api_shared.so`는 `/usr/local/lib`에 위치하거나 `ldconfig` cache에 등록돼
   있어야 합니다(있으면 `LD_LIBRARY_PATH` 없이도 로드됩니다). 없으면 replay 실행 전에
   `LD_LIBRARY_PATH=/opt/3fs/lib`를 지정합니다.

### 검증

backend가 로드되고 usrbio 드라이버가 열리는지를 가장 빠르게 확인하려면 fire L2
prepare를 직접 실행합니다.

```bash
source .venv/bin/activate
python -m replayer.main \
  --trace <trace-root>/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/hf3fs.yaml \
  --speedup 2 \
  --trace-percent 100 \
  --l2-path /mnt/l2/hf3fs \
  --output-dir /tmp/hf3fs-check \
  --prepare-l2 --prepare-only
```

정상 동작하면 `L2 prepare complete`, 그리고 replay log에 `createBackend` 이후
`DynamicNixlStoreL2Adapter`가 기록됩니다. 과거 실패 시그니처:

- `Plugin file does not exist: .../libplugin_HF3FS.so` → 플러그인 미배포 또는 잘못된
  cu12/cu13 디렉터리에 배포.
- `createBackend: unsupported backend 'HF3FS'` → 플러그인 없음 (위와 동일 근본 원인).
- `Failed to create engine: boost::filesystem::canonical ... "/mnt/3fs/"` →
  `mount_point` 미설정 또는 HF3FS mount path 불일치.

## 3FS vs HF3FS backend

두 backend는 같은 storage(HF3FS)를 서로 다른 I/O 경로로 사용합니다.

| | `fs_native` (3FS) | `nixl_store_dynamic` (HF3FS) |
| --- | --- | --- |
| I/O 경로 | HF3FS FUSE mount, POSIX 파일 I/O | usrbio driver, user-space RDMA I/O |
| 커널 page cache | 사용 | 우회 |
| L2 namespace | FUSE에서 일반 파일로 보임 (`du` 측정 가능) | usrbio 채널에 기록 (`du`로는 0) |
| config | `configs/replayer/3fs.yaml` | `configs/replayer/hf3fs.yaml` |

성능 비교를 원하면 speedup graph를 두 backend로 각각 실행해
`sweep-summary.csv`의 throughput을 비교합니다. 기존 `3fs.yaml`(`fs_native`) 경로는
건드리지 않고, HF3FS는 별도 `mount_point` 경로(`/3fs/stage`)로 운용합니다.
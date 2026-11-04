// k6 的 gRPC 負載腳本，由 tools/load_bench.py 呼叫，不單獨使用。
//
// 請求本體由驅動程式以環境變數 BENCH_DATA（JSON 陣列）傳入，不經檔案：題目文字不落地。
// 每個 VU 依序取下一筆，所以同一階段內每題被送出的次數相同（shared-iterations）。
// 逐請求的耗時與狀態由驅動程式從 `--out json` 的 grpc_req_duration 點讀出，摘要不在這裡算。
import grpc from 'k6/net/grpc';
import exec from 'k6/execution';

const data = JSON.parse(__ENV.BENCH_DATA);
const ids = JSON.parse(__ENV.BENCH_IDS);
const method = __ENV.BENCH_METHOD;
const vus = parseInt(__ENV.BENCH_VUS, 10);
const iterations = parseInt(__ENV.BENCH_ITERATIONS, 10);

const client = new grpc.Client();
client.load([__ENV.BENCH_PROTO_ROOT], __ENV.BENCH_PROTO);

export const options = {
  scenarios: {
    stage: {
      executor: 'shared-iterations',
      vus,
      iterations,
      // 逾時的請求記為錯誤；期限由請求本身的 timeout 決定，這裡只是保險。
      maxDuration: '30m',
    },
  },
  // 驅動程式讀原始點，k6 自己的摘要只留給人看。
  summaryTrendStats: ['med', 'p(95)', 'p(99)', 'max'],
};

let connected = false;

export default function () {
  if (!connected) {
    // 每個 VU 一條連線，與服務間的長連線用法相同；連線建立不計入請求耗時。
    client.connect(__ENV.BENCH_TARGET, { plaintext: true, timeout: '10s' });
    connected = true;
  }
  const i = exec.scenario.iterationInTest % data.length;
  client.invoke(method, data[i], {
    metadata: { 'x-tenant-id': 'bench', 'x-request-id': `bench-${ids[i]}` },
    timeout: __ENV.BENCH_TIMEOUT,
    tags: { qid: ids[i] },
  });
}

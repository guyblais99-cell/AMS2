using GameReaderCommon;
using SimHub.Plugins;
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace Ams2HubCapturePlugin
{
    [PluginDescription("Provides normalized SimHub telemetry to AMS2 Career Hub Companion.")]
    [PluginAuthor("AMS2 Career Hub")]
    [PluginName("AMS2 Career Hub Capture")]
    public class CapturePlugin : IPlugin, IDataPlugin
    {
        private const int Port = 17891;
        private readonly JavaScriptSerializer serializer = new JavaScriptSerializer();
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private TcpListener listener;
        private volatile string latestJson = "{\"gameRunning\":false,\"session\":\"\"}";
        private DateTime lastSnapshotUtc = DateTime.MinValue;

        public PluginManager PluginManager { get; set; }

        public void Init(PluginManager pluginManager)
        {
            try
            {
                listener = new TcpListener(IPAddress.Loopback, Port);
                listener.Start();
                Task.Run(() => ListenAsync(cancellation.Token));
                SimHub.Logging.Current.Info($"AMS2 Career Hub telemetry endpoint listening on 127.0.0.1:{Port}.");
            }
            catch (Exception error)
            {
                SimHub.Logging.Current.Error("AMS2 Career Hub could not start its local telemetry endpoint.", error);
            }
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
            var now = DateTime.UtcNow;
            if ((now - lastSnapshotUtc).TotalMilliseconds < 200)
                return;
            lastSnapshotUtc = now;

            try
            {
                var status = data.NewData;
                var session = data.GameRunning && !data.GameInMenu && status != null
                    ? status.SessionTypeName ?? string.Empty
                    : string.Empty;

                var snapshot = new Dictionary<string, object>
                {
                    ["gameRunning"] = data.GameRunning,
                    ["session"] = session,
                    ["sessionId"] = data.SessionId.ToString(),
                    ["game"] = data.GameName ?? string.Empty,
                    ["track"] = status?.TrackName ?? string.Empty,
                    ["car"] = status?.CarModel ?? string.Empty,
                    ["carClass"] = status?.CarClass ?? string.Empty,
                    ["position"] = status?.Position ?? 0,
                    ["opponentsCount"] = status?.OpponentsCount ?? 0
                };

                latestJson = serializer.Serialize(snapshot);
            }
            catch (Exception error)
            {
                SimHub.Logging.Current.Error("AMS2 Career Hub could not read the current SimHub frame.", error);
            }
        }

        public void End(PluginManager pluginManager)
        {
            cancellation.Cancel();
            listener?.Stop();
        }

        private async Task ListenAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                TcpClient client;
                try
                {
                    client = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
                }
                catch (SocketException)
                {
                    if (token.IsCancellationRequested)
                        break;
                    continue;
                }
                catch (ObjectDisposedException)
                {
                    break;
                }

                _ = Task.Run(() => RespondAsync(client));
            }
        }

        private async Task RespondAsync(TcpClient client)
        {
            using (client)
            using (var stream = client.GetStream())
            using (var reader = new System.IO.StreamReader(stream, Encoding.ASCII, false, 1024, true))
            {
                try
                {
                    var requestLine = await reader.ReadLineAsync().ConfigureAwait(false);
                    if (string.IsNullOrEmpty(requestLine))
                        return;
                    string header;
                    do
                    {
                        header = await reader.ReadLineAsync().ConfigureAwait(false);
                    } while (!string.IsNullOrEmpty(header));

                    var body = Encoding.UTF8.GetBytes(latestJson);
                    var responseHeader = Encoding.ASCII.GetBytes(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n" +
                        "Cache-Control: no-store\r\nConnection: close\r\nContent-Length: " + body.Length + "\r\n\r\n");
                    await stream.WriteAsync(responseHeader, 0, responseHeader.Length).ConfigureAwait(false);
                    await stream.WriteAsync(body, 0, body.Length).ConfigureAwait(false);
                }
                catch (Exception error) when (error is IOException || error is ObjectDisposedException)
                {
                }
            }
        }
    }
}
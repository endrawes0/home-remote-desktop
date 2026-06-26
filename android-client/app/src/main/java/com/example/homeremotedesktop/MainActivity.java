package com.example.homeremotedesktop;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.text.InputType;
import android.view.MotionEvent;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {
    private static final int DISCOVERY_PORT = 51333;
    private static final int DEFAULT_TCP_PORT = 51334;
    private static final byte[] DISCOVER_REQUEST = "HRD_DISCOVER_V1".getBytes(StandardCharsets.UTF_8);

    private final List<ServerInfo> servers = new ArrayList<>();
    private ArrayAdapter<String> serverAdapter;
    private Spinner serverSpinner;
    private EditText hostField;
    private EditText portField;
    private EditText passcodeField;
    private EditText textField;
    private TextView statusView;
    private LinearLayout controls;
    private Button connectButton;
    private Button fullscreenButton;
    private DesktopView desktopView;
    private RemoteConnection connection;
    private boolean fullscreen;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
    }

    @Override
    protected void onDestroy() {
        if (connection != null) {
            connection.close();
        }
        super.onDestroy();
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(18, 18, 18));

        controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.VERTICAL);
        controls.setPadding(16, 16, 16, 10);
        controls.setBackgroundColor(Color.rgb(245, 245, 245));

        serverSpinner = new Spinner(this);
        serverAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, new ArrayList<>());
        serverAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        serverSpinner.setAdapter(serverAdapter);
        serverSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (position >= 0 && position < servers.size()) {
                    ServerInfo server = servers.get(position);
                    hostField.setText(server.host);
                    portField.setText(String.valueOf(server.port));
                }
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        controls.addView(serverSpinner, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        hostField = new EditText(this);
        hostField.setHint("Server IP");
        hostField.setSingleLine(true);
        hostField.setInputType(InputType.TYPE_CLASS_TEXT);
        row.addView(hostField, new LinearLayout.LayoutParams(0, -2, 1));
        portField = new EditText(this);
        portField.setHint("Port");
        portField.setSingleLine(true);
        portField.setText(String.valueOf(DEFAULT_TCP_PORT));
        portField.setInputType(InputType.TYPE_CLASS_NUMBER);
        row.addView(portField, new LinearLayout.LayoutParams(180, -2));
        controls.addView(row);

        passcodeField = new EditText(this);
        passcodeField.setHint("Passcode");
        passcodeField.setSingleLine(true);
        passcodeField.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        controls.addView(passcodeField, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        Button discoverButton = new Button(this);
        discoverButton.setAllCaps(false);
        discoverButton.setText("Find");
        discoverButton.setOnClickListener(v -> discoverServers());
        buttons.addView(discoverButton, new LinearLayout.LayoutParams(0, -2, 1));
        connectButton = new Button(this);
        connectButton.setAllCaps(false);
        connectButton.setText("Connect");
        connectButton.setOnClickListener(v -> toggleConnection());
        buttons.addView(connectButton, new LinearLayout.LayoutParams(0, -2, 1));
        fullscreenButton = new Button(this);
        fullscreenButton.setAllCaps(false);
        fullscreenButton.setText("Full");
        fullscreenButton.setOnClickListener(v -> setFullscreen(!fullscreen));
        buttons.addView(fullscreenButton, new LinearLayout.LayoutParams(0, -2, 1));
        controls.addView(buttons);

        LinearLayout textRow = new LinearLayout(this);
        textRow.setOrientation(LinearLayout.HORIZONTAL);
        textField = new EditText(this);
        textField.setHint("Text to type");
        textField.setSingleLine(true);
        textField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        textRow.addView(textField, new LinearLayout.LayoutParams(0, -2, 1));
        Button typeButton = new Button(this);
        typeButton.setAllCaps(false);
        typeButton.setText("Type");
        typeButton.setOnClickListener(v -> sendText());
        textRow.addView(typeButton, new LinearLayout.LayoutParams(-2, -2));
        Button enterButton = new Button(this);
        enterButton.setAllCaps(false);
        enterButton.setText("Enter");
        enterButton.setOnClickListener(v -> sendPress("Return"));
        textRow.addView(enterButton, new LinearLayout.LayoutParams(-2, -2));
        controls.addView(textRow);

        statusView = new TextView(this);
        statusView.setTextColor(Color.rgb(40, 40, 40));
        statusView.setText("Ready");
        controls.addView(statusView);

        desktopView = new DesktopView(this);
        desktopView.setInputSender(message -> {
            RemoteConnection active = connection;
            if (active != null) {
                active.sendInput(message);
            }
        });
        desktopView.setFullscreenToggle(() -> setFullscreen(!fullscreen));

        root.addView(controls, new LinearLayout.LayoutParams(-1, -2));
        root.addView(desktopView, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
    }

    private void discoverServers() {
        setStatus("Searching LAN...");
        new Thread(() -> {
            WifiManager.MulticastLock lock = null;
            DatagramSocket socket = null;
            List<ServerInfo> found = new ArrayList<>();
            try {
                WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
                if (wifi != null) {
                    lock = wifi.createMulticastLock("hrd-discovery");
                    lock.setReferenceCounted(false);
                    lock.acquire();
                }
                socket = new DatagramSocket();
                socket.setBroadcast(true);
                socket.setSoTimeout(350);
                DatagramPacket request = new DatagramPacket(
                        DISCOVER_REQUEST,
                        DISCOVER_REQUEST.length,
                        InetAddress.getByName("255.255.255.255"),
                        DISCOVERY_PORT
                );
                socket.send(request);
                long deadline = System.currentTimeMillis() + 2200;
                byte[] buffer = new byte[4096];
                while (System.currentTimeMillis() < deadline) {
                    DatagramPacket response = new DatagramPacket(buffer, buffer.length);
                    try {
                        socket.receive(response);
                    } catch (SocketTimeoutException ignored) {
                        continue;
                    }
                    JSONObject object = new JSONObject(new String(response.getData(), response.getOffset(), response.getLength(), StandardCharsets.UTF_8));
                    if (!"hrd_announce_v1".equals(object.optString("type"))) {
                        continue;
                    }
                    String host = response.getAddress().getHostAddress();
                    int port = object.optInt("port", DEFAULT_TCP_PORT);
                    String name = object.optString("name", host);
                    boolean exists = false;
                    for (ServerInfo item : found) {
                        if (item.host.equals(host) && item.port == port) {
                            exists = true;
                            break;
                        }
                    }
                    if (!exists) {
                        found.add(new ServerInfo(name, host, port));
                    }
                }
            } catch (Exception ex) {
                showToast("Discovery failed: " + ex.getMessage());
            } finally {
                if (socket != null) {
                    socket.close();
                }
                if (lock != null && lock.isHeld()) {
                    lock.release();
                }
            }
            runOnUiThread(() -> updateServers(found));
        }).start();
    }

    private void updateServers(List<ServerInfo> found) {
        servers.clear();
        servers.addAll(found);
        serverAdapter.clear();
        for (ServerInfo server : servers) {
            serverAdapter.add(server.name + " (" + server.host + ":" + server.port + ")");
        }
        serverAdapter.notifyDataSetChanged();
        if (!servers.isEmpty()) {
            serverSpinner.setSelection(0);
            setStatus("Found " + servers.size() + " server(s)");
        } else {
            setStatus("No servers found");
        }
    }

    private void toggleConnection() {
        if (connection != null) {
            disconnect();
        } else {
            connect();
        }
    }

    private void connect() {
        String host = hostField.getText().toString().trim();
        String passcode = passcodeField.getText().toString().trim();
        if (host.isEmpty() || passcode.isEmpty()) {
            showToast("Enter host and passcode");
            return;
        }
        int port;
        try {
            port = Integer.parseInt(portField.getText().toString().trim());
        } catch (NumberFormatException ex) {
            showToast("Invalid port");
            return;
        }
        disconnect();
        setStatus("Connecting...");
        RemoteConnection next = new RemoteConnection(host, port, passcode);
        connection = next;
        updateConnectionButton();
        next.start();
    }

    private void disconnect() {
        RemoteConnection active = connection;
        if (active != null) {
            active.close();
        }
        connection = null;
        setStatus("Disconnected");
        updateConnectionButton();
    }

    private void sendText() {
        RemoteConnection active = connection;
        if (active == null) {
            showToast("Not connected");
            return;
        }
        String text = textField.getText().toString();
        if (text.isEmpty()) {
            return;
        }
        try {
            JSONObject message = new JSONObject();
            message.put("event", "text");
            message.put("text", text);
            active.sendInput(message);
            textField.setText("");
        } catch (Exception ignored) {
        }
    }

    private void sendPress(String keysym) {
        RemoteConnection active = connection;
        if (active == null) {
            showToast("Not connected");
            return;
        }
        try {
            JSONObject message = new JSONObject();
            message.put("event", "press");
            message.put("keysym", keysym);
            active.sendInput(message);
        } catch (Exception ignored) {
        }
    }

    private void setFullscreen(boolean enabled) {
        fullscreen = enabled;
        controls.setVisibility(enabled ? View.GONE : View.VISIBLE);
        fullscreenButton.setText(enabled ? "Exit" : "Full");
        int flags = enabled
                ? View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                : View.SYSTEM_UI_FLAG_LAYOUT_STABLE;
        getWindow().getDecorView().setSystemUiVisibility(flags);
    }

    private void setStatus(String status) {
        runOnUiThread(() -> statusView.setText(status));
    }

    private void updateConnectionButton() {
        runOnUiThread(() -> connectButton.setText(connection == null ? "Connect" : "Stop"));
    }

    private void showToast(String message) {
        runOnUiThread(() -> Toast.makeText(this, message, Toast.LENGTH_LONG).show());
    }

    private static final class ServerInfo {
        final String name;
        final String host;
        final int port;

        ServerInfo(String name, String host, int port) {
            this.name = name;
            this.host = host;
            this.port = port;
        }
    }

    private final class RemoteConnection extends Thread {
        private final String host;
        private final int port;
        private final String passcode;
        private final Object sendLock = new Object();
        private volatile boolean running = true;
        private Socket socket;
        private OutputStream output;
        private Bitmap desktopBitmap;

        RemoteConnection(String host, int port, String passcode) {
            this.host = host;
            this.port = port;
            this.passcode = passcode;
        }

        @Override
        public void run() {
            try {
                socket = new Socket();
                socket.connect(new InetSocketAddress(host, port), 8000);
                socket.setTcpNoDelay(true);
                socket.setSoTimeout(0);
                output = socket.getOutputStream();
                DataInputStream input = new DataInputStream(socket.getInputStream());

                JSONObject hello = new JSONObject();
                hello.put("type", "hello");
                hello.put("passcode", passcode);
                hello.put("client", "android");
                sendPacket(output, hello, null);

                Packet authPacket = readPacket(input);
                if (!authPacket.header.optBoolean("ok", false)) {
                    throw new IOException(authPacket.header.optString("error", "authentication failed"));
                }
                setStatus("Connected to " + host + ":" + port);
                while (running) {
                    Packet packet = readPacket(input);
                    if (!"frame".equals(packet.header.optString("type"))) {
                        continue;
                    }
                    Bitmap frame = applyFrame(packet.header, packet.payload);
                    desktopView.setBitmap(frame);
                }
            } catch (Exception ex) {
                if (running) {
                    setStatus("Disconnected: " + ex.getMessage());
                }
            } finally {
                close();
                if (connection == this) {
                    connection = null;
                    updateConnectionButton();
                }
            }
        }

        void sendInput(JSONObject message) {
            if (!running || output == null) {
                return;
            }
            try {
                JSONObject envelope = new JSONObject(message.toString());
                envelope.put("type", "input");
                synchronized (sendLock) {
                    sendPacket(output, envelope, null);
                }
            } catch (Exception ignored) {
            }
        }

        void close() {
            running = false;
            try {
                if (socket != null) {
                    socket.close();
                }
            } catch (IOException ignored) {
            }
        }

        private Bitmap applyFrame(JSONObject header, byte[] payload) throws Exception {
            String mode = header.optString("mode", "full");
            if ("full".equals(mode)) {
                Bitmap decoded = BitmapFactory.decodeByteArray(payload, 0, payload.length);
                if (decoded == null) {
                    throw new IOException("failed to decode JPEG frame");
                }
                desktopBitmap = decoded.copy(Bitmap.Config.ARGB_8888, true);
                return desktopBitmap;
            }

            int width = header.optInt("image_w", 1);
            int height = header.optInt("image_h", 1);
            if (desktopBitmap == null || desktopBitmap.getWidth() != width || desktopBitmap.getHeight() != height) {
                desktopBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
            } else if (!desktopBitmap.isMutable()) {
                desktopBitmap = desktopBitmap.copy(Bitmap.Config.ARGB_8888, true);
            }
            Canvas canvas = new Canvas(desktopBitmap);
            JSONArray tiles = header.optJSONArray("tiles");
            int offset = 0;
            if (tiles != null) {
                for (int i = 0; i < tiles.length(); i++) {
                    JSONObject tile = tiles.getJSONObject(i);
                    int size = tile.getInt("size");
                    Bitmap tileBitmap = BitmapFactory.decodeByteArray(payload, offset, size);
                    offset += size;
                    if (tileBitmap != null) {
                        canvas.drawBitmap(tileBitmap, tile.getInt("x"), tile.getInt("y"), null);
                    }
                }
            }
            return desktopBitmap;
        }
    }

    private static Packet readPacket(DataInputStream input) throws Exception {
        int headerLength = input.readInt();
        if (headerLength <= 0 || headerLength > 1024 * 1024) {
            throw new IOException("invalid header length: " + headerLength);
        }
        byte[] headerBytes = new byte[headerLength];
        input.readFully(headerBytes);
        JSONObject header = new JSONObject(new String(headerBytes, StandardCharsets.UTF_8));
        int payloadLength = header.optInt("payload_len", 0);
        if (payloadLength < 0) {
            throw new IOException("invalid payload length");
        }
        byte[] payload = new byte[payloadLength];
        if (payloadLength > 0) {
            input.readFully(payload);
        }
        return new Packet(header, payload);
    }

    private static void sendPacket(OutputStream output, JSONObject header, byte[] payload) throws Exception {
        byte[] body = payload == null ? new byte[0] : payload;
        header.put("payload_len", body.length);
        byte[] encodedHeader = header.toString().getBytes(StandardCharsets.UTF_8);
        output.write((encodedHeader.length >>> 24) & 0xff);
        output.write((encodedHeader.length >>> 16) & 0xff);
        output.write((encodedHeader.length >>> 8) & 0xff);
        output.write(encodedHeader.length & 0xff);
        output.write(encodedHeader);
        if (body.length > 0) {
            output.write(body);
        }
        output.flush();
    }

    private static final class Packet {
        final JSONObject header;
        final byte[] payload;

        Packet(JSONObject header, byte[] payload) {
            this.header = header;
            this.payload = payload;
        }
    }

    public static final class DesktopView extends View {
        private final Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
        private Bitmap bitmap;
        private RectF drawRect = new RectF();
        private InputSender inputSender;
        private Runnable fullscreenToggle;
        private boolean dragging;
        private float downX;
        private float downY;
        private float downNx;
        private float downNy;
        private long downAt;
        private final float dragThreshold;

        public DesktopView(Activity activity) {
            super(activity);
            setBackgroundColor(Color.rgb(12, 12, 12));
            dragThreshold = 12f * getResources().getDisplayMetrics().density;
        }

        void setInputSender(InputSender inputSender) {
            this.inputSender = inputSender;
        }

        void setFullscreenToggle(Runnable fullscreenToggle) {
            this.fullscreenToggle = fullscreenToggle;
        }

        void setBitmap(Bitmap bitmap) {
            post(() -> {
                this.bitmap = bitmap;
                invalidate();
            });
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            Bitmap current = bitmap;
            if (current == null) {
                Paint textPaint = new Paint();
                textPaint.setColor(Color.LTGRAY);
                textPaint.setTextSize(36);
                canvas.drawText("No frame", 32, 64, textPaint);
                drawRect.setEmpty();
                return;
            }
            float scale = Math.min((float) getWidth() / current.getWidth(), (float) getHeight() / current.getHeight());
            float drawWidth = current.getWidth() * scale;
            float drawHeight = current.getHeight() * scale;
            float left = (getWidth() - drawWidth) / 2f;
            float top = (getHeight() - drawHeight) / 2f;
            drawRect.set(left, top, left + drawWidth, top + drawHeight);
            canvas.drawBitmap(current, new Rect(0, 0, current.getWidth(), current.getHeight()), drawRect, paint);
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            int action = event.getActionMasked();
            if (event.getPointerCount() >= 2 && action == MotionEvent.ACTION_POINTER_DOWN) {
                if (fullscreenToggle != null) {
                    fullscreenToggle.run();
                }
                return true;
            }
            if (inputSender == null || drawRect.isEmpty()) {
                return true;
            }
            float x = event.getX();
            float y = event.getY();
            if (!drawRect.contains(x, y)) {
                return true;
            }
            float nx = (x - drawRect.left) / drawRect.width();
            float ny = (y - drawRect.top) / drawRect.height();
            try {
                if (action == MotionEvent.ACTION_DOWN) {
                    dragging = false;
                    downX = x;
                    downY = y;
                    downNx = clamp(nx);
                    downNy = clamp(ny);
                    downAt = System.currentTimeMillis();
                    sendPointer("move", nx, ny);
                } else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
                    if (dragging) {
                        sendPointer("up", nx, ny);
                    } else if (action == MotionEvent.ACTION_UP && System.currentTimeMillis() - downAt < 800) {
                        sendPointer("click", nx, ny);
                    }
                    dragging = false;
                } else if (action == MotionEvent.ACTION_MOVE) {
                    float dx = x - downX;
                    float dy = y - downY;
                    if (!dragging && Math.hypot(dx, dy) >= dragThreshold) {
                        dragging = true;
                        sendPointer("down", downNx, downNy);
                    }
                    sendPointer("move", nx, ny);
                } else {
                    return true;
                }
            } catch (Exception ignored) {
            }
            return true;
        }

        private void sendPointer(String event, float nx, float ny) throws Exception {
            JSONObject message = new JSONObject();
            message.put("event", event);
            message.put("button", "left");
            message.put("nx", clamp(nx));
            message.put("ny", clamp(ny));
            inputSender.send(message);
        }

        private static float clamp(float value) {
            if (value < 0f) {
                return 0f;
            }
            if (value > 1f) {
                return 1f;
            }
            return value;
        }
    }

    interface InputSender {
        void send(JSONObject message);
    }
}

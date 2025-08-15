/*
  multi_fan_led_serial_control.ino
  ────────────────────────────────────────────────────────
  - 라즈베리파이 USB 시리얼 명령으로 5개 팬(0~100%) + 3색 LED 4개 제어
  - 팬 핀:
      BIG(4핀 PWM) = D9 (OC1A, Timer1, 25kHz, Active-Low 듀티 반전)
      MOSFET CH1..CH4 = D3, D5, D6, D11 (PWM)
  - LED 핀 (공통 캐소드, 실제 배선 RGB):
      LED1: R=D2,  G=D7,  B=D4
      LED2: R=D8,  G=D12, B=D10
      LED3: R=A0,  G=A2,  B=A1
      LED4: R=A3,  G=A5,  B=A4

  - 프로토콜(개행 '\n' 종료):
      SETF <f1> <f2> <f3> <f4> <big>         → 팬 5개 동시 갱신 (0~100)
      SETL <c1> <c2> <c3> <c4>               → LED 4개 색상 (R|G|B|W|OFF)
      SETALL <f1> <f2> <f3> <f4> <big> <c1> <c2> <c3> <c4>   → 원샷 갱신 (옵션)
      GET?                                    → 현재 상태 보고
    * 응답:
      ACK:SETF:f1,f2,f3,f4,big
      ACK:SETL:c1,c2,c3,c4
      ACK:SETALL:f1,f2,f3,f4,big;c1,c2,c3,c4
      DATA:STATE:F:f1,f2,f3,f4,big;L:c1,c2,c3,c4

  - 오류:
      ERR:BAD_ARGS / ERR:OUT_OF_RANGE / ERR:BAD_COLOR / ERR:UNKNOWN_CMD

  ⚠ 하드웨어 주의:
    - 4핀 팬 PWM(D9)은 오픈드레인/오픈컬렉터(2N7000 등)로 GND로만 당김 권장
    - 공통 캐소드 LED: 각 R/G/B에 220~330Ω 직렬저항, 캐소드는 GND
*/

#include <Arduino.h>

// ===== Fan pins =====
const uint8_t FAN_PWM_PIN = 9;   // BIG fan (OC1A)
const uint8_t MOSFET_1    = 3;   // CH1
const uint8_t MOSFET_2    = 5;   // CH2
const uint8_t MOSFET_3    = 6;   // CH3
const uint8_t MOSFET_4    = 11;  // CH4

// ===== LED pins (RGB) =====
const uint8_t LED_PINS[4][3] = {
  {2,  7,  4},     // LED1: R,G,B
  {8,  12, 10},    // LED2: R,G,B
  {A0, A2, A1},    // LED3: R,G,B
  {A3, A5, A4}     // LED4: R,G,B
};

// ===== State =====
uint8_t FANs[5] = {0,0,0,0,0};  // CH1..CH4, BIG
enum LedColor { LED_OFF=0, LED_R, LED_G, LED_B, LED_W };
LedColor LEDs[4] = {LED_OFF, LED_OFF, LED_OFF, LED_OFF};

// ===== Utils =====
static inline bool inRange(int v) { return v>=0 && v<=100; }

static inline void fan_pwm25k_init_OC1A() {
  TCCR1A = 0; TCCR1B = 0; TCNT1 = 0;
  ICR1   = 639;                      // 25 kHz
  OCR1A  = 0;
  TCCR1A = _BV(COM1A1) | _BV(WGM11); // OC1A enable + Fast PWM (mode 14)
  TCCR1B = _BV(WGM13)  | _BV(WGM12) | _BV(CS10); // no prescale
  pinMode(FAN_PWM_PIN, OUTPUT);
}

static inline void setBigPercent(uint8_t pct) {
  if (pct>100) pct=100;
  uint8_t inv = 100 - pct;                         // Active-Low
  OCR1A = (uint32_t)(ICR1 + 1) * inv / 100;
  FANs[4] = pct;
}

static inline void setMosfetPercent(uint8_t pin, uint8_t pct, uint8_t idx) {
  if (pct>100) pct=100;
  analogWrite(pin, (uint8_t)(pct * 2.55));
  FANs[idx] = pct;                                  // idx:0..3
}

static inline void applyFans(uint8_t f1,uint8_t f2,uint8_t f3,uint8_t f4,uint8_t big) {
  setMosfetPercent(MOSFET_1, f1, 0);
  setMosfetPercent(MOSFET_2, f2, 1);
  setMosfetPercent(MOSFET_3, f3, 2);
  setMosfetPercent(MOSFET_4, f4, 3);
  setBigPercent(big);
}

static inline void applyLedColor(uint8_t ledIdx, LedColor c) {
  // RGB pins order: [R,G,B]
  bool r=false,g=false,b=false;
  switch(c){
    case LED_R: r=true; break;
    case LED_G: g=true; break;
    case LED_B: b=true; break;
    case LED_W: r=g=b=true; break;
    default: break; // OFF
  }
  digitalWrite(LED_PINS[ledIdx][0], r?HIGH:LOW);
  digitalWrite(LED_PINS[ledIdx][1], g?HIGH:LOW);
  digitalWrite(LED_PINS[ledIdx][2], b?HIGH:LOW);
  LEDs[ledIdx]=c;
}

static inline LedColor parseColor(const char* s) {
  if (!s) return LED_OFF;
  if (!strcmp(s,"R"))   return LED_R;
  if (!strcmp(s,"G"))   return LED_G;
  if (!strcmp(s,"B"))   return LED_B;
  if (!strcmp(s,"W"))   return LED_W;
  if (!strcmp(s,"OFF")) return LED_OFF;
  return (LedColor)255; // invalid
}

static inline const char* colorName(LedColor c){
  switch(c){
    case LED_R: return "R";
    case LED_G: return "G";
    case LED_B: return "B";
    case LED_W: return "W";
    default:    return "OFF";
  }
}

String readLine() {
  static String buf="";
  while(Serial.available()){
    char c=(char)Serial.read();
    if(c=='\n'){ String line=buf; buf=""; return line; }
    if(c!='\r') buf+=c;
  }
  return String("");
}

void printState(){
  Serial.print(F("DATA:STATE:F:"));
  Serial.print(FANs[0]); Serial.print(',');
  Serial.print(FANs[1]); Serial.print(',');
  Serial.print(FANs[2]); Serial.print(',');
  Serial.print(FANs[3]); Serial.print(',');
  Serial.print(FANs[4]);
  Serial.print(F(";L:"));
  Serial.print(colorName(LEDs[0])); Serial.print(',');
  Serial.print(colorName(LEDs[1])); Serial.print(',');
  Serial.print(colorName(LEDs[2])); Serial.print(',');
  Serial.println(colorName(LEDs[3]));
}

void setup() {
  Serial.begin(115200);
  while(!Serial){}

  fan_pwm25k_init_OC1A();
  pinMode(MOSFET_1, OUTPUT);
  pinMode(MOSFET_2, OUTPUT);
  pinMode(MOSFET_3, OUTPUT);
  pinMode(MOSFET_4, OUTPUT);

  for(int i=0;i<4;i++){
    for(int c=0;c<3;c++){
      pinMode(LED_PINS[i][c], OUTPUT);
      digitalWrite(LED_PINS[i][c], LOW);
    }
  }

  applyFans(0,0,0,0,0);
  for(int i=0;i<4;i++) applyLedColor(i, LED_OFF);

  Serial.println(F("READY"));
}

void loop() {
  String line = readLine();
  if(!line.length()) return;

  line.trim();
  String up=line; up.toUpperCase();

  // SETF f1 f2 f3 f4 big
  if(up.startsWith("SETF ")){
    int f1,f2,f3,f4,big;
    int n = sscanf(line.c_str(), "SETF %d %d %d %d %d", &f1,&f2,&f3,&f4,&big);
    if(n!=5){ Serial.println(F("ERR:BAD_ARGS")); return; }
    if(!inRange(f1)||!inRange(f2)||!inRange(f3)||!inRange(f4)||!inRange(big)){
      Serial.println(F("ERR:OUT_OF_RANGE")); return;
    }
    applyFans((uint8_t)f1,(uint8_t)f2,(uint8_t)f3,(uint8_t)f4,(uint8_t)big);
    Serial.print(F("ACK:SETF:"));
    Serial.print(FANs[0]); Serial.print(',');
    Serial.print(FANs[1]); Serial.print(',');
    Serial.print(FANs[2]); Serial.print(',');
    Serial.print(FANs[3]); Serial.print(',');
    Serial.println(FANs[4]);
    return;
  }

  // SETL c1 c2 c3 c4   (R|G|B|W|OFF)
  if(up.startsWith("SETL ")){
    char c1[6],c2[6],c3[6],c4[6];
    int n = sscanf(line.c_str(), "SETL %5s %5s %5s %5s", c1,c2,c3,c4);
    if(n!=4){ Serial.println(F("ERR:BAD_ARGS")); return; }
    for(int i=0;i<5;i++){ if(c1[i]) c1[i]=toupper(c1[i]); }
    for(int i=0;i<5;i++){ if(c2[i]) c2[i]=toupper(c2[i]); }
    for(int i=0;i<5;i++){ if(c3[i]) c3[i]=toupper(c3[i]); }
    for(int i=0;i<5;i++){ if(c4[i]) c4[i]=toupper(c4[i]); }

    LedColor L[4] = { parseColor(c1), parseColor(c2), parseColor(c3), parseColor(c4) };
    if(L[0]==255||L[1]==255||L[2]==255||L[3]==255){ Serial.println(F("ERR:BAD_COLOR")); return; }

    for(int i=0;i<4;i++) applyLedColor(i, L[i]);

    Serial.print(F("ACK:SETL:"));
    Serial.print(colorName(LEDs[0])); Serial.print(',');
    Serial.print(colorName(LEDs[1])); Serial.print(',');
    Serial.print(colorName(LEDs[2])); Serial.print(',');
    Serial.println(colorName(LEDs[3]));
    return;
  }

  // SETALL f1 f2 f3 f4 big c1 c2 c3 c4   (옵션)
  if(up.startsWith("SETALL ")){
    int f1,f2,f3,f4,big;
    char c1[6],c2[6],c3[6],c4[6];
    int n = sscanf(line.c_str(), "SETALL %d %d %d %d %d %5s %5s %5s %5s",
                   &f1,&f2,&f3,&f4,&big, c1,c2,c3,c4);
    if(n!=9){ Serial.println(F("ERR:BAD_ARGS")); return; }
    if(!inRange(f1)||!inRange(f2)||!inRange(f3)||!inRange(f4)||!inRange(big)){
      Serial.println(F("ERR:OUT_OF_RANGE")); return;
    }
    for(int i=0;i<5;i++){ if(c1[i]) c1[i]=toupper(c1[i]); }
    for(int i=0;i<5;i++){ if(c2[i]) c2[i]=toupper(c2[i]); }
    for(int i=0;i<5;i++){ if(c3[i]) c3[i]=toupper(c3[i]); }
    for(int i=0;i<5;i++){ if(c4[i]) c4[i]=toupper(c4[i]); }
    LedColor L[4] = { parseColor(c1), parseColor(c2), parseColor(c3), parseColor(c4) };
    if(L[0]==255||L[1]==255||L[2]==255||L[3]==255){ Serial.println(F("ERR:BAD_COLOR")); return; }

    applyFans((uint8_t)f1,(uint8_t)f2,(uint8_t)f3,(uint8_t)f4,(uint8_t)big);
    for(int i=0;i<4;i++) applyLedColor(i, L[i]);

    Serial.print(F("ACK:SETALL:"));
    Serial.print(FANs[0]); Serial.print(',');
    Serial.print(FANs[1]); Serial.print(',');
    Serial.print(FANs[2]); Serial.print(',');
    Serial.print(FANs[3]); Serial.print(',');
    Serial.print(FANs[4]); Serial.print(';');
    Serial.print(colorName(LEDs[0])); Serial.print(',');
    Serial.print(colorName(LEDs[1])); Serial.print(',');
    Serial.print(colorName(LEDs[2])); Serial.print(',');
    Serial.println(colorName(LEDs[3]));
    return;
  }

  // GET?
  if(up=="GET?"){ printState(); return; }

  Serial.println(F("ERR:UNKNOWN_CMD"));
}

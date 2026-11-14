/* md5 compacto (RFC 1321) para ubicar el shard detail/<md5(id)[:2]>.json.
   crypto.subtle no trae md5. Entrada: string (se codifica en UTF-8). Salida: hex. */
(function (g) {
  'use strict';
  var S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  var K = new Int32Array(64);
  for (var i = 0; i < 64; i++) K[i] = (Math.abs(Math.sin(i + 1)) * 4294967296) | 0;

  function md5(str) {
    var bytes = new TextEncoder().encode(str);
    var n = bytes.length;
    var words = ((n + 8) >>> 6) + 1;
    var M = new Int32Array(words * 16);
    for (var j = 0; j < n; j++) M[j >> 2] |= bytes[j] << ((j % 4) * 8);
    M[n >> 2] |= 0x80 << ((n % 4) * 8);
    M[words * 16 - 2] = n * 8;

    var a0 = 0x67452301, b0 = 0xefcdab89 | 0, c0 = 0x98badcfe | 0, d0 = 0x10325476;
    for (var off = 0; off < M.length; off += 16) {
      var A = a0, B = b0, C = c0, D = d0;
      for (var t = 0; t < 64; t++) {
        var F, gi, r = t >> 4;
        if (r === 0) { F = (B & C) | (~B & D); gi = t; }
        else if (r === 1) { F = (D & B) | (~D & C); gi = (5 * t + 1) & 15; }
        else if (r === 2) { F = B ^ C ^ D; gi = (3 * t + 5) & 15; }
        else { F = C ^ (B | ~D); gi = (7 * t) & 15; }
        var tmp = D; D = C; C = B;
        var x = (A + F + K[t] + M[off + gi]) | 0;
        var s = S[(r << 2) | (t & 3)];
        B = (B + ((x << s) | (x >>> (32 - s)))) | 0;
        A = tmp;
      }
      a0 = (a0 + A) | 0; b0 = (b0 + B) | 0; c0 = (c0 + C) | 0; d0 = (d0 + D) | 0;
    }
    var out = '';
    [a0, b0, c0, d0].forEach(function (v) {
      for (var k = 0; k < 4; k++) out += ((v >>> (k * 8)) & 255).toString(16).padStart(2, '0');
    });
    return out;
  }
  g.md5 = md5;
})(typeof window !== 'undefined' ? window : globalThis);

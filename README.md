<!DOCTYPE html>
<html lang="en">
<head>
    <link rel="stylesheet" href="style.css">
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>1 Quiz</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            margin: 0;
            min-height: 100vh;
            background-color: #749676;
            position: relative;
            overflow: hidden;
        }
        .background-video {
            position: fixed;
            top: 50%;
            left: 50%;
            width: 100%;
            height: 100%;
            object-fit: cover;
            transform: translate(-50%, -50%);
            z-index: -1;
        }
        .content {
            position: relative;
            z-index: 1;
            color: white;
            padding: 60px 20px;
        }
        button {
            padding: 10px 20px;
            margin: 10px;
            font-size: 16px;
            cursor: pointer;
        }
        a {
            color: #fff;
            text-decoration: underline;
        }
        .highlight {
            font-weight: bold;
            color: #e6d543;
        }
    </style>    
</head>
<body>
    <video class="background-video" autoplay muted loop playsinline>
        <source src="file:///C:/Users/cabal/Downloads/20556585-hd_1920_1080_24fps.mp4" type="video/mp4">
        Your browser does not support the video tag.
    </video>
    <div class="content">
        <h1><em>Which is <span class="highlight">faster</span></em></h1>
        <h3>If the <span class="highlight">light</span> and <span class="highlight">sound</span> are released at the same time, which will reach you first?</h3>
        <button id="light">Light</button>
        <button id="sound">Sound</button>
        <p><a href="https://en.wikipedia.org/wiki/Speed_of_light" target="_blank" rel="noopener">Click here to learn more</a></p>
    </div>
<script>
    const lightButton = document.getElementById('light');
    const soundButton = document.getElementById('sound');

    lightButton.addEventListener('click', () => {
        alert('Light is faster than sound!');
    });

    soundButton.addEventListener('click', () => {
        alert('Sound is slower than light!');
    });
</script>
</body>
</html>
